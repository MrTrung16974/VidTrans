from __future__ import annotations

from typing import Any, Sequence


MAX_NATURAL_TEMPO = 1.08
TEMPO_DEADBAND = 1.03
MAX_SYNTHESIS_RATE_BOOST_PERCENT = 12
MAX_BORROWED_PAUSE_SECONDS = 0.45
MIN_TRANSITION_GAP_SECONDS = 0.08
VOICE_GAP_SECONDS = 0.025


def bounded_tempo(rendered_duration: float, cue_duration: float) -> float:
    """Return a subtle sync correction without making speech sound rushed."""

    if rendered_duration <= 0 or cue_duration <= 0:
        return 1.0
    required = rendered_duration / cue_duration
    if required <= TEMPO_DEADBAND:
        return 1.0
    return min(required, MAX_NATURAL_TEMPO)


def adaptive_synthesis_rate_percent(rendered_duration: float, cue_duration: float) -> int:
    """Prefer TTS-native pacing over a large post-process tempo shift."""

    if rendered_duration <= 0 or cue_duration <= 0:
        return 0
    required = rendered_duration / cue_duration
    if required <= TEMPO_DEADBAND:
        return 0
    return min(MAX_SYNTHESIS_RATE_BOOST_PERCENT, max(1, round((required - 1.0) * 100)))


def available_voice_window(segments: Sequence[dict[str, Any]], index: int) -> float:
    """Return a natural TTS window, borrowing only a bounded real pause.

    A translated sentence may be slightly longer than its source cue. When the
    source has a genuine pause before the next sentence, using part of that
    pause sounds more natural than accelerating the voice unnecessarily.
    """

    segment = segments[index]
    source_start = max(0.0, float(segment.get("start") or 0.0))
    source_end = max(source_start, float(segment.get("end") or source_start))
    base_window = max(0.25, source_end - source_start - 0.05)
    if index + 1 >= len(segments):
        return base_window
    next_start = max(source_end, float(segments[index + 1].get("start") or source_end))
    usable_pause = max(0.0, next_start - source_end - MIN_TRANSITION_GAP_SECONDS)
    return base_window + min(usable_pause, MAX_BORROWED_PAUSE_SECONDS)


def schedule_voice_segments(
    segments: Sequence[dict[str, Any]],
    *,
    video_duration: float,
) -> list[dict[str, float]]:
    """Place natural-speed clips without overlapping consecutive speakers.

    Every clip remains anchored to its source start when possible. If the
    previous Vietnamese line is still speaking, the next line starts just
    after it. A real pause in the source automatically resets accumulated
    delay because the desired start will again be later than the previous end.
    """

    schedule: list[dict[str, float]] = []
    previous_end = 0.0
    for index, segment in enumerate(segments):
        if not segment.get("tts_path"):
            continue
        source_start = max(0.0, float(segment.get("start") or 0.0))
        duration = max(0.0, float(segment.get("tts_duration") or 0.0))
        if duration <= 0:
            continue
        start = max(source_start, previous_end + VOICE_GAP_SECONDS if schedule else source_start)
        if start >= video_duration:
            break
        end = start + duration
        item = {
            "segment_index": float(index),
            "source_start": source_start,
            "start": start,
            "end": end,
            "drift": max(0.0, start - source_start),
        }
        schedule.append(item)
        previous_end = end
        segment["tts_start"] = round(start, 3)
        segment["tts_end"] = round(end, 3)
        segment["tts_drift"] = round(item["drift"], 3)
    return schedule
