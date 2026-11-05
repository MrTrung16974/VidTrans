"""Move a few words across subtitle line breaks that cut a word or phrase in two.

Whisper's per-character timing is off by a character or so, and caption alignment
and length-based splitting place line breaks by that timing. In unspaced scripts the
break then often falls inside a word: 像极了他追查多年的通缉 / 犯虽然已经过去了十多年
(job 50272532, whose burned-in caption reads 像极了他追查多年的通缉犯). Each line is
translated as a unit, so the translator either mistranslates the pieces or carries
meaning over to the neighbouring line and every following line drifts by one.

A language model proposes the natural breaks; nothing it returns is trusted beyond
that. Its lines must hold exactly the original characters in order, every break may
move only a few word tokens, and only onto a token edge of the recognised words, so
the words, their timing and every character the recogniser heard stay as they were.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol, Sequence

from pipeline.subtitle_segmentation import _joined_text


logger = logging.getLogger(__name__)

# A break further from its timed position than this is a rewrite, not a repair.
MAX_SHIFT_TOKENS = 3
# Lines further apart than this are separate utterances; their break is real.
MAX_JOIN_GAP_SECONDS = 0.5
BATCH_LINES = 25


class BoundaryFixer(Protocol):
    def rebalance_lines(self, lines: list[str]) -> list[str]: ...


def _has_unspaced_script(text: str) -> bool:
    return any("㐀" <= character <= "鿿" or "぀" <= character <= "ヿ" for character in text)


def _squash(text: str) -> str:
    return "".join(str(text).split())


def _tokens(cue: dict[str, Any]) -> list[str] | None:
    """Word tokens of the cue when they spell exactly its text, else None."""

    words = [_squash(word.get("word") or "") for word in cue.get("words") or []]
    words = [word for word in words if word]
    if not words or "".join(words) != _squash(_source(cue)):
        return None
    return words


def _source(cue: dict[str, Any]) -> str:
    return str(cue.get("source_text") or cue.get("text") or "")


def _eligible(cue: dict[str, Any]) -> bool:
    return (
        not cue.get("skip_subtitle")
        and _has_unspaced_script(_source(cue))
        and _tokens(cue) is not None
    )


def _joinable(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return float(right["start"]) - float(left["end"]) <= MAX_JOIN_GAP_SECONDS


def _apply_run(
    run: list[dict[str, Any]], proposed: Sequence[str]
) -> tuple[list[dict[str, Any]], int]:
    """Rebuild a run of joinable cues with the validated breaks of ``proposed``."""

    original = [_squash(_source(cue)) for cue in run]
    joined = "".join(original)
    lines = [_squash(line) for line in proposed]
    if len(lines) != len(run):
        return run, 0
    token_lists = [_tokens(cue) or [] for cue in run]
    flat_words: list[dict[str, Any]] = []
    edges: list[int] = [0]  # character offset after each token
    for cue in run:
        for word in cue.get("words") or []:
            if _squash(word.get("word") or ""):
                flat_words.append(word)
                edges.append(edges[-1] + len(_squash(word.get("word") or "")))
    edge_index = {offset: index for index, offset in enumerate(edges)}

    old_breaks: list[int] = []
    position = 0
    for tokens in token_lists[:-1]:
        position += len(tokens)
        old_breaks.append(position)
    # Each proposed line is checked where it starts: it must spell exactly the original
    # characters from there. A line the model altered (a dropped or changed character)
    # keeps its old break, and the next line is checked from that break, so one bad
    # line costs only its own repair, not the whole batch's.
    new_breaks: list[int] = []
    cursor = 0  # token index where the current line starts
    for line, old in zip(lines[:-1], old_breaks):
        start = edges[cursor]
        token = edge_index.get(start + len(line))
        # A changed line, a break inside a recognised token, or one moved too far keeps its place.
        if (
            not line
            or joined[start : start + len(line)] != line
            or token is None
            or abs(token - old) > MAX_SHIFT_TOKENS
            or token <= cursor
        ):
            token = old
        new_breaks.append(token)
        cursor = token
    # Kept and moved breaks must still leave every line at least one token.
    bounds = [0, *new_breaks, len(flat_words)]
    if any(right <= left for left, right in zip(bounds, bounds[1:])):
        return run, 0

    changed = 0
    output: list[dict[str, Any]] = []
    for index, cue in enumerate(run):
        first, last = bounds[index], bounds[index + 1]
        old_first = 0 if index == 0 else old_breaks[index - 1]
        old_last = len(flat_words) if index == len(run) - 1 else old_breaks[index]
        if (first, last) == (old_first, old_last):
            output.append(cue)
            continue
        words = [dict(word) for word in flat_words[first:last]]
        rebuilt = dict(cue)
        text = _joined_text(words)
        rebuilt["text"] = text
        if cue.get("source_text"):
            rebuilt["source_text"] = text
        rebuilt["words"] = words
        # A caption-timed line keeps the caption's window; speech lines follow their words.
        if cue.get("timing_source") != "caption":
            starts = [float(word["start"]) for word in words if word.get("start") is not None]
            ends = [float(word["end"]) for word in words if word.get("end") is not None]
            if starts and ends:
                rebuilt["start"] = min(starts)
                rebuilt["end"] = max(max(ends), min(starts) + 0.1)
        rebuilt["boundary_repaired"] = True
        output.append(rebuilt)
        changed += 1
    return output, changed


def _runs(segments: Sequence[dict[str, Any]]) -> list[list[int]]:
    runs: list[list[int]] = []
    current: list[int] = []
    for index, cue in enumerate(segments):
        if not _eligible(cue):
            if len(current) >= 2:
                runs.append(current)
            current = []
            continue
        if current and not _joinable(segments[current[-1]], cue):
            if len(current) >= 2:
                runs.append(current)
            current = []
        current.append(index)
    if len(current) >= 2:
        runs.append(current)
    return runs


def repair_cue_boundaries(
    segments: Sequence[dict[str, Any]],
    fixer: BoundaryFixer,
    *,
    check_active: Callable[[], None] = lambda: None,
    batch_lines: int = BATCH_LINES,
) -> tuple[list[dict[str, Any]], int]:
    """Return the cues with misplaced breaks moved, and how many cues changed.

    Runs of joinable cues are sent together in batches; a batch that fails or
    returns anything but the same characters keeps its breaks unchanged.
    """

    output = [dict(segment) for segment in segments]
    batches: list[list[int]] = []
    for run in _runs(output):
        for start in range(0, len(run), batch_lines):
            chunk = run[start : start + batch_lines]
            if len(chunk) >= 2:
                batches.append(chunk)
    changed = 0
    for batch in batches:
        check_active()
        lines = [_squash(_source(output[index])) for index in batch]
        try:
            proposed = fixer.rebalance_lines(lines)
        except Exception as exc:
            logger.warning("Line break repair skipped for %d cue(s): %s", len(batch), type(exc).__name__)
            continue
        # Joinable sub-runs inside the batch are rebuilt independently.
        rebuilt, count = _apply_run([output[index] for index in batch], proposed)
        for index, cue in zip(batch, rebuilt):
            output[index] = cue
        changed += count
    return output, changed
