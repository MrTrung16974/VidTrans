from __future__ import annotations

from typing import Any, Sequence


SUBTITLE_TAIL_HOLD_SECONDS = 0.18
SUBTITLE_TRANSITION_GAP_SECONDS = 0.02


def _is_visual_cue(segment: dict[str, Any]) -> bool:
    return (
        segment.get("source_method") == "ocr"
        or segment.get("modality") == "visual"
        or segment.get("timing_source") == "caption"
    )


def subtitle_render_window(
    segments: Sequence[dict[str, Any]], index: int
) -> tuple[float, float]:
    """Return the on-screen window for one translated subtitle.

    A translated subtitle sits on or next to the source caption, so it must
    appear exactly when the source cue starts, even if dubbing drifts the
    voice later. Captions read from the picture end with the source caption.
    Speech cues stay up until the later of the source cue and the scheduled
    voice, plus a small tail hold. Either way the window is clipped before the next visible
    cue so two captions never collide.
    """

    segment = segments[index]
    start = max(0.0, float(segment.get("start", 0.0)))
    source_end = max(start + 0.05, float(segment.get("end", start)))
    end = source_end
    if not _is_visual_cue(segment):
        voice_end = float(segment.get("tts_end") or source_end)
        end = max(source_end, voice_end) + SUBTITLE_TAIL_HOLD_SECONDS

    for following in segments[index + 1 :]:
        if following.get("skip_subtitle"):
            continue
        next_start = max(0.0, float(following.get("start", end)))
        if next_start > start:
            end = min(end, max(start + 0.05, next_start - SUBTITLE_TRANSITION_GAP_SECONDS))
        break

    return start, end
