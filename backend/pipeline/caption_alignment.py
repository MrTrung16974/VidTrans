"""Align speech cues to burned-in captions without running OCR.

Whisper segments follow the voice, so they start and end at different
moments than the captions burned into the picture and often break lines
elsewhere. When a video has burned-in captions, a cheap stroke-mask scan
finds the caption band and the exact frames where each caption appears and
disappears; Whisper words are then regrouped into those caption windows.
"""

from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from pipeline.ocr import _subtitle_change_signature
from pipeline.subtitle_segmentation import _joined_text


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptionAlignmentConfig:
    roi_top: float = 0.68
    roi_bottom: float = 0.96
    # Pass 1 samples sparsely to locate the caption band.
    scan_fps: float = 5.0
    scan_width: int = 320
    # Pass 2 reads only the band, frame by frame, for exact boundaries.
    frame_rate: float = 30.0
    detail_scale: float = 1 / 3
    # A caption changes when consecutive band masks overlap less than this.
    change_overlap: float = 0.4
    # Presence threshold between caption-free and speech band density.
    presence_ratio: float = 0.25
    dropout_frames: int = 2
    min_caption_seconds: float = 0.27
    # Speech usually leads its caption; search this far for the lag.
    max_speech_lead: float = 0.6
    word_snap_seconds: float = 0.3
    # Whisper stretches the first word after a pause back over the silence. A word
    # longer than this many times the typical per-character duration is placed by
    # its spoken tail, so "肯" of "肯定" is not pulled into the previous caption.
    stretched_word_ratio: float = 2.0
    # Below this share of words inside captions the video is treated as
    # having no burned-in captions and Whisper timing is kept.
    min_word_coverage: float = 0.6

    def validate(self) -> None:
        if not 0 <= self.roi_top < self.roi_bottom <= 1:
            raise ValueError("caption ROI must satisfy 0 <= top < bottom <= 1")
        if not 0 < self.scan_fps <= self.frame_rate <= 120:
            raise ValueError("caption scan rates must satisfy 0 < scan_fps <= frame_rate <= 120")
        if self.scan_width < 32 or not 0 < self.detail_scale <= 1:
            raise ValueError("caption scan resolution is invalid")


def _mask_overlap(left: Any, right: Any) -> float:
    import numpy as np

    union = np.count_nonzero(left | right)
    if union == 0:
        return 1.0
    return np.count_nonzero(left & right) / union


def _decode_masks(
    video_path: Path,
    *,
    ffmpeg: str,
    video_filter: str,
    width: int,
    height: int,
) -> Iterator[Any]:
    from PIL import Image

    process = subprocess.Popen(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(video_path),
            "-vf",
            video_filter,
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    frame_bytes = width * height
    try:
        assert process.stdout is not None
        while True:
            buffer = process.stdout.read(frame_bytes)
            if len(buffer) < frame_bytes:
                break
            yield _subtitle_change_signature(Image.frombytes("L", (width, height), buffer), width)
    finally:
        if process.stdout is not None:
            process.stdout.close()
        process.kill()
        process.wait()


def _speech_flags(
    times: Sequence[float],
    segments: Sequence[dict[str, Any]],
    margin: float,
) -> Any:
    import numpy as np

    flags = np.zeros(len(times), dtype=bool)
    values = np.asarray(times, dtype=float)
    for segment in segments:
        start = float(segment.get("start") or 0.0) - margin
        end = float(segment.get("end") or 0.0) + margin
        flags |= (values >= start) & (values < end)
    return flags


def locate_caption_band(
    masks: Any,
    times: Sequence[float],
    segments: Sequence[dict[str, Any]],
) -> tuple[float, float, float, float] | None:
    """Return the caption band as (top, bottom, left, right) fractions of the ROI.

    Captions accompany speech, so the rows and columns that gain stroke
    pixels while someone speaks are the caption band; scenery does not care
    whether anyone is talking.
    """

    import numpy as np

    if masks.ndim != 3 or len(masks) == 0:
        return None
    speech = _speech_flags(times, segments, 0.0)
    if np.count_nonzero(speech) < 10:
        return None
    silent = ~_speech_flags(times, segments, 1.0)
    height, width = masks.shape[1:]

    def baseline(profile: Any) -> Any:
        if np.count_nonzero(silent) >= 25:
            return profile[silent].mean(axis=0)
        # Continuous narration: rows are still empty between captions.
        return np.percentile(profile[speech], 20, axis=0)

    rows = masks.mean(axis=2)
    row_gain = rows[speech].mean(axis=0) - baseline(rows)
    peak = int(np.argmax(row_gain))
    if row_gain[peak] < 0.01:
        return None
    top = bottom = peak
    while top > 0 and row_gain[top - 1] >= row_gain[peak] * 0.25:
        top -= 1
    while bottom < height - 1 and row_gain[bottom + 1] >= row_gain[peak] * 0.25:
        bottom += 1

    columns = masks[:, top : bottom + 1, :].mean(axis=1)
    column_gain = columns[speech].mean(axis=0) - baseline(columns)
    active = np.flatnonzero(column_gain >= max(column_gain.max(), 1e-9) * 0.15)
    if active.size == 0:
        return None
    return top / height, (bottom + 1) / height, active[0] / width, (active[-1] + 1) / width


def caption_intervals(
    density: Sequence[float],
    changes: Sequence[float],
    times: Sequence[float],
    segments: Sequence[dict[str, Any]],
    config: CaptionAlignmentConfig,
) -> list[tuple[float, float]]:
    """Turn per-frame band density and frame-to-frame overlap into caption windows."""

    import numpy as np

    values = np.asarray(density, dtype=float)
    if values.size == 0:
        return []
    speech = _speech_flags(times, segments, 0.0)
    silent = ~_speech_flags(times, segments, 1.0)
    if not speech.any():
        return []
    floor = (
        float(np.median(values[silent]))
        if np.count_nonzero(silent) >= 25
        else float(np.percentile(values[speech], 20))
    )
    spoken = float(np.median(values[speech]))
    if spoken <= floor:
        return []
    threshold = floor + config.presence_ratio * (spoken - floor)
    visible = values >= threshold

    frame_step = 1.0 / config.frame_rate
    windows: list[tuple[int, int]] = []
    start: int | None = None
    count = len(values)
    for index in range(count):
        if visible[index] and start is not None and changes[index] < config.change_overlap:
            windows.append((start, index))
            start = index
            continue
        if visible[index] and start is None:
            start = index
        elif not visible[index] and start is not None:
            if visible[index + 1 : index + 1 + config.dropout_frames].any():
                continue
            windows.append((start, index))
            start = None
    if start is not None:
        windows.append((start, count))
    minimum = config.min_caption_seconds / frame_step
    return [
        (float(times[first]), float(times[last - 1]) + frame_step)
        for first, last in windows
        if last - first >= minimum
    ]


def _words(segments: Sequence[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    return [
        (index, word)
        for index, segment in enumerate(segments)
        for word in segment.get("words") or []
        if str(word.get("word") or "").strip()
        and word.get("start") is not None
        and word.get("end") is not None
    ]


def _spoken_spans(
    words: Sequence[tuple[int, dict[str, Any]]], config: CaptionAlignmentConfig
) -> list[tuple[float, float]]:
    """Word spans with silence absorbed into stretched words removed from their start."""

    def characters(word: dict[str, Any]) -> int:
        return max(1, len(str(word.get("word") or "").strip()))

    spans = [(float(word["start"]), float(word["end"])) for _, word in words]
    rates = sorted((end - start) / characters(word) for (start, end), (_, word) in zip(spans, words) if end > start)
    if not rates:
        return spans
    typical = rates[len(rates) // 2]
    result = []
    for (start, end), (_, word) in zip(spans, words):
        expected = config.stretched_word_ratio * typical * characters(word)
        result.append((max(start, end - expected), end) if end - start > expected else (start, end))
    return result


def _speech_lead(
    words: Sequence[tuple[int, dict[str, Any]]],
    intervals: Sequence[tuple[float, float]],
    config: CaptionAlignmentConfig,
) -> tuple[float, int]:
    """Find the lag that puts the most word midpoints inside captions."""

    import numpy as np

    starts = np.asarray([interval[0] for interval in intervals])
    ends = np.asarray([interval[1] for interval in intervals])
    midpoints = np.asarray([(start + end) / 2 for start, end in _spoken_spans(words, config)])
    best_offset, best_count = 0.0, -1
    steps = int(round(config.max_speech_lead / 0.02))
    # Smaller lags first so ties keep the timing closest to Whisper.
    for step in sorted(range(-steps, steps + 1), key=abs):
        offset = step * 0.02
        shifted = midpoints + offset
        slot = np.searchsorted(starts, shifted, side="right") - 1
        inside = (slot >= 0) & (shifted <= ends[np.clip(slot, 0, None)])
        count = int(np.count_nonzero(inside))
        if count > best_count:
            best_offset, best_count = offset, count
    return best_offset, best_count


def align_words_to_captions(
    segments: Sequence[dict[str, Any]],
    intervals: Sequence[tuple[float, float]],
    config: CaptionAlignmentConfig | None = None,
) -> list[dict[str, Any]] | None:
    """Regroup Whisper words into caption windows, or None when they do not fit."""

    config = config or CaptionAlignmentConfig()
    words = _words(segments)
    if not words or not intervals:
        return None
    intervals = sorted(intervals)
    offset, contained = _speech_lead(words, intervals, config)
    coverage = contained / len(words)
    if coverage < config.min_word_coverage:
        logger.info("Caption alignment skipped: only %.0f%% of words fall inside captions", coverage * 100)
        return None

    assigned: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    leftovers: list[tuple[int, dict[str, Any]]] = []
    for item, (spoken_start, spoken_end) in zip(words, _spoken_spans(words, config)):
        start = spoken_start + offset
        end = spoken_end + offset
        overlap, slot = max(
            (min(end, last) - max(start, first), index)
            for index, (first, last) in enumerate(intervals)
        )
        if overlap <= 0:
            midpoint = (start + end) / 2
            distance, slot = min(
                (min(abs(midpoint - first), abs(midpoint - last)), index)
                for index, (first, last) in enumerate(intervals)
            )
            if distance > config.word_snap_seconds:
                leftovers.append(item)
                continue
        assigned.setdefault(slot, []).append(item)

    output: list[dict[str, Any]] = []
    for slot, items in sorted(assigned.items()):
        first, last = intervals[slot]
        source_indexes = sorted({index for index, _ in items})
        cue = dict(segments[source_indexes[0]])
        cue.update(
            {
                "start": round(first, 3),
                "end": round(last, 3),
                "text": _joined_text([word for _, word in items]),
                "words": [dict(word) for _, word in items],
                "avg_logprob": sum(
                    float(segments[index].get("avg_logprob") or 0.0) for index in source_indexes
                )
                / len(source_indexes),
                "source_method": "speech",
                "timing_source": "caption",
            }
        )
        output.append(cue)

    # Speech outside every caption keeps Whisper timing, trimmed so it never
    # overlaps a caption window.
    groups: list[list[tuple[int, dict[str, Any]]]] = []
    for item in leftovers:
        if groups and groups[-1][-1][0] == item[0]:
            groups[-1].append(item)
        else:
            groups.append([item])
    for group in groups:
        start = float(group[0][1]["start"])
        end = float(group[-1][1]["end"])
        for first, last in intervals:
            if first <= start < last:
                start = last
            if first < end <= last:
                end = first
        if end - start < 0.1:
            continue
        cue = dict(segments[group[0][0]])
        cue.update(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "text": _joined_text([word for _, word in group]),
                "words": [dict(word) for _, word in group],
            }
        )
        output.append(cue)

    with_words = {index for index, _ in words}
    output.extend(dict(segment) for index, segment in enumerate(segments) if index not in with_words)
    logger.info(
        "Aligned %d speech cue(s) to burned-in captions (speech lead %.2fs, %.0f%% words inside)",
        len(assigned),
        offset,
        coverage * 100,
    )
    return sorted(output, key=lambda item: (float(item["start"]), float(item["end"])))


def _even(value: float) -> int:
    return max(2, int(math.floor(value / 2)) * 2)


def detect_caption_intervals(
    video_path: Path,
    segments: Sequence[dict[str, Any]],
    *,
    ffmpeg: str,
    frame_size: tuple[int, int],
    config: CaptionAlignmentConfig,
) -> list[tuple[float, float]]:
    import numpy as np

    config.validate()
    source_width, source_height = frame_size
    if source_width <= 0 or source_height <= 0:
        return []
    roi_y = _even(source_height * config.roi_top) if config.roi_top > 0 else 0
    roi_height = _even(source_height * (config.roi_bottom - config.roi_top))
    scan_width = min(config.scan_width, source_width)
    scan_height = _even(roi_height * scan_width / source_width)
    masks = np.stack(
        list(
            _decode_masks(
                video_path,
                ffmpeg=ffmpeg,
                video_filter=(
                    f"fps={config.scan_fps:.6f},crop={source_width}:{roi_height}:0:{roi_y},"
                    f"scale={scan_width}:{scan_height},format=gray"
                ),
                width=scan_width,
                height=scan_height,
            )
        )
        or [np.zeros((scan_height, scan_width), dtype=bool)]
    )
    # The fps filter shows each sample half an interval after its slot.
    scan_times = [(index + 0.5) / config.scan_fps for index in range(len(masks))]
    band = locate_caption_band(masks, scan_times, segments)
    if band is None:
        return []

    top, bottom, left, right = band
    # Keep the crop tight: static scenery inside it dilutes the mask change
    # when one caption replaces another at the same position.
    pad_y = 1.0 / scan_height
    pad_x = 0.0
    crop_y = roi_y + _even(max(0.0, top - pad_y) * roi_height)
    crop_bottom = roi_y + min(roi_height, int(math.ceil(min(1.0, bottom + pad_y) * roi_height)))
    crop_x = _even(max(0.0, left - pad_x) * source_width)
    crop_right = min(source_width, int(math.ceil(min(1.0, right + pad_x) * source_width)))
    crop_width = _even(crop_right - crop_x)
    crop_height = _even(crop_bottom - crop_y)
    detail_width = _even(max(16, crop_width * config.detail_scale))
    detail_height = _even(max(8, crop_height * config.detail_scale))

    density: list[float] = []
    changes: list[float] = []
    previous = None
    for mask in _decode_masks(
        video_path,
        ffmpeg=ffmpeg,
        video_filter=(
            f"fps={config.frame_rate:.6f},crop={crop_width}:{crop_height}:{crop_x}:{crop_y},"
            f"scale={detail_width}:{detail_height},format=gray"
        ),
        width=detail_width,
        height=detail_height,
    ):
        density.append(float(mask.mean()))
        changes.append(1.0 if previous is None else _mask_overlap(mask, previous))
        previous = mask
    times = [index / config.frame_rate for index in range(len(density))]
    return caption_intervals(density, changes, times, segments, config)


def align_speech_to_burned_captions(
    video_path: Path,
    segments: list[dict[str, Any]],
    *,
    ffmpeg: str,
    frame_size: tuple[int, int],
    config: CaptionAlignmentConfig | None = None,
) -> list[dict[str, Any]]:
    """Return caption-aligned speech cues, or the input when there are no captions."""

    config = config or CaptionAlignmentConfig()
    intervals = detect_caption_intervals(
        video_path,
        segments,
        ffmpeg=ffmpeg,
        frame_size=frame_size,
        config=config,
    )
    if not intervals:
        logger.info("No burned-in caption band found; keeping Whisper timing")
        return segments
    return align_words_to_captions(segments, intervals, config) or segments
