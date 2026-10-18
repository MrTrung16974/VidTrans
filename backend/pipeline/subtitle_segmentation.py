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
    if len(words) < 2:
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
