from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence


_SENTENCE_END_RE = re.compile(r"[。！？!?…]+[\"'”’）】》]?$")
_CLAUSE_END_RE = re.compile(r"[，、；：,;:]+[\"'”’）】》]?$")


@dataclass(frozen=True)
class SubtitleSegmentationOptions:
    preferred_chars: int = 14
    max_chars: int = 20
    pause_seconds: float = 0.65
    max_duration_seconds: float = 3.2
    min_duration: float = 0.10

    def validate(self) -> None:
        if not 4 <= self.preferred_chars <= self.max_chars <= 120:
            raise ValueError("subtitle character limits must satisfy 4 <= preferred <= max <= 120")
        if not 0.1 <= self.pause_seconds <= 5.0:
            raise ValueError("pause_seconds must be between 0.1 and 5.0")
        if not 1.0 <= self.max_duration_seconds <= 15.0:
            raise ValueError("max_duration_seconds must be between 1.0 and 15.0")
        if not 0.01 <= self.min_duration <= 1.0:
            raise ValueError("min_duration must be between 0.01 and 1.0")


def _word_text(word: dict[str, Any]) -> str:
    return str(word.get("word") or "").strip()


def _joined_text(words: Sequence[dict[str, Any]]) -> str:
    values = [_word_text(word) for word in words]
    values = [value for value in values if value]
    if not values:
        return ""
    # Whisper emits leading spaces for space-delimited languages. After the
    # caller strips those, Han/Thai tokens should be joined without inventing
    # spaces; Latin-script tokens remain readable with spaces.
    if any(" " in str(word.get("word") or "") for word in words):
        return " ".join(values)
    if all(re.search(r"[\u3400-\u9fff\u3040-\u30ff\u0e00-\u0e7f]", value) for value in values):
        return "".join(values)
    return " ".join(values)


def _split_timed_segment(
    segment: dict[str, Any], options: SubtitleSegmentationOptions
) -> list[dict[str, Any]]:
    words = [word for word in (segment.get("words") or []) if _word_text(word)]
    # A cue aligned to a burned-in caption already has the caption's line
    # breaks and timing; splitting it would desynchronise it again.
    if len(words) < 2 or segment.get("timing_source") == "caption":
        return [dict(segment)]

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for index, word in enumerate(words):
        current.append(word)
        text = _joined_text(current)
        next_word = words[index + 1] if index + 1 < len(words) else None
        gap = 0.0
        if next_word is not None:
            gap = max(0.0, float(next_word.get("start") or 0.0) - float(word.get("end") or 0.0))
        sentence_end = bool(_SENTENCE_END_RE.search(_word_text(word)))
        clause_end = bool(_CLAUSE_END_RE.search(_word_text(word)))
        natural_boundary = sentence_end or gap >= options.pause_seconds
        readable_boundary = clause_end and len(text) >= options.preferred_chars
        hard_boundary = len(text) >= options.max_chars
        timed_boundary = (
            float(word.get("end") or 0.0) - float(current[0].get("start") or 0.0)
            >= options.max_duration_seconds
        )
        if natural_boundary or readable_boundary or hard_boundary or timed_boundary:
            groups.append(current)
            current = []
    if current:  # Explicit final flush prevents the last spoken phrase being lost.
        groups.append(current)

    if len(groups) == 1:
        return [dict(segment)]

    output: list[dict[str, Any]] = []
    segment_start = float(segment.get("start") or 0.0)
    segment_end = float(segment.get("end") or segment_start + options.min_duration)
    for split_index, group in enumerate(groups):
        start = max(segment_start, float(group[0].get("start") or segment_start))
        end = min(segment_end, float(group[-1].get("end") or segment_end))
        end = max(start + options.min_duration, end)
        cue = dict(segment)
        cue.update(
            {
                "start": start,
                "end": end,
                "text": _joined_text(group),
                "words": [dict(word) for word in group],
                "source_segment_index": segment.get("source_segment_index"),
                "source_split_index": split_index,
            }
        )
        output.append(cue)
    return output


def segment_subtitle_cues(
    segments: Sequence[dict[str, Any]],
    *,
    options: SubtitleSegmentationOptions | None = None,
) -> list[dict[str, Any]]:
    """Split long ASR cues only where word timing provides a real boundary.

    Cues without word timestamps (for example OCR cues) are preserved instead
    of receiving synthetic, evenly divided timestamps.
    """

    resolved = options or SubtitleSegmentationOptions()
    resolved.validate()
    output: list[dict[str, Any]] = []
    for source_index, original in enumerate(segments):
        source = dict(original)
        source["source_segment_index"] = source_index
        split = _split_timed_segment(source, resolved)
        for split_index, cue in enumerate(split):
            cue["source_segment_index"] = source_index
            cue["source_split_index"] = split_index
            cue["cue_id"] = f"cue-{source_index:06d}-{split_index:03d}"
            output.append(cue)
    return output


_UNSPACED_SCRIPT_RE = re.compile(r"[぀-ヿ㐀-鿿豈-﫿฀-๿]")


def _letters(text: str) -> int:
    return sum(1 for character in text if character.isalpha())


def _join_cue_texts(left: str, right: str) -> str:
    left, right = left.strip(), right.strip()
    if not left or not right:
        return left or right
    unspaced = _UNSPACED_SCRIPT_RE.match(left[-1]) and _UNSPACED_SCRIPT_RE.match(right[0])
    return f"{left}{right}" if unspaced else f"{left} {right}"


def _can_absorb(cue: dict[str, Any]) -> bool:
    return not cue.get("skip_subtitle")


def _absorb(target: dict[str, Any], fragment: dict[str, Any], *, fragment_first: bool) -> dict[str, Any]:
    first, second = (fragment, target) if fragment_first else (target, fragment)
    merged = dict(target)
    # Caption alignment leaves unmatched syllables between captions; the caption's
    # own timing stays authoritative so it remains in sync with the burned-in text.
    if target.get("timing_source") != "caption":
        merged["start"] = min(float(first["start"]), float(second["start"]))
        merged["end"] = max(float(first["end"]), float(second["end"]))
    merged["text"] = _join_cue_texts(str(first.get("text") or ""), str(second.get("text") or ""))
    if first.get("source_text") or second.get("source_text"):
        merged["source_text"] = _join_cue_texts(
            str(first.get("source_text") or first.get("text") or ""),
            str(second.get("source_text") or second.get("text") or ""),
        )
    if first.get("words") or second.get("words"):
        merged["words"] = [*(first.get("words") or []), *(second.get("words") or [])]
    if fragment.get("needs_review"):
        merged["needs_review"] = True
        merged.setdefault("review_reason", fragment.get("review_reason"))
    return merged


def merge_fragment_cues(
    cues: Sequence[dict[str, Any]],
    *,
    max_letters: int = 2,
    max_gap_seconds: float = 0.65,
) -> list[dict[str, Any]]:
    """Fold one- or two-letter cues into the closest neighbouring cue.

    Whisper sometimes splits a word across segments (海 / 森 / 堡 for
    Heisenberg). Alone, such a fragment means nothing to a translator, which
    echoes it back and fails the whole job; with its neighbour it translates.
    """

    output: list[dict[str, Any]] = []
    pending = [dict(cue) for cue in cues]
    for index, cue in enumerate(pending):
        text = str(cue.get("source_text") or cue.get("text") or "")
        fragment = _can_absorb(cue) and cue.get("timing_source") != "caption"
        if not fragment or not 1 <= _letters(text) <= max_letters:
            output.append(cue)
            continue
        previous = output[-1] if output and _can_absorb(output[-1]) else None
        following = pending[index + 1] if index + 1 < len(pending) and _can_absorb(pending[index + 1]) else None
        # A finished sentence does not continue into the fragment.
        if previous is not None and _SENTENCE_END_RE.search(str(previous.get("text") or "").strip()):
            previous = None
        gap_before = float(cue["start"]) - float(previous["end"]) if previous is not None else None
        gap_after = float(following["start"]) - float(cue["end"]) if following is not None else None
        candidates = [
            (gap, side) for gap, side in ((gap_before, "previous"), (gap_after, "following"))
            if gap is not None and gap <= max_gap_seconds
        ]
        if not candidates:
            output.append(cue)
            continue
        _, side = min(candidates, key=lambda item: item[0])
        if side == "previous":
            output[-1] = _absorb(previous, cue, fragment_first=False)
        else:
            pending[index + 1] = _absorb(following, cue, fragment_first=True)
    return output
