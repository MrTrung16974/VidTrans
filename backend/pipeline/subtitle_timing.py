from __future__ import annotations

from typing import Any, Sequence


SUBTITLE_TAIL_HOLD_SECONDS = 0.18
SUBTITLE_TRANSITION_GAP_SECONDS = 0.02


def subtitle_render_window(
    segments: Sequence[dict[str, Any]], index: int
) -> tuple[float, float]:
    """Return the on-screen window for one translated subtitle.

    Dubbing can move a translated line later than its source cue.  All render
    backends must therefore follow the scheduled TTS window when it exists.
    A small tail hold keeps the final word readable after the audio fade, but
    is clipped before the next visible cue so two captions never collide.
    """

    segment = segments[index]
    start = max(0.0, float(segment.get("tts_start", segment.get("start", 0.0))))
    base_end = max(start + 0.05, float(segment.get("tts_end", segment.get("end", start))))
    end = base_end + SUBTITLE_TAIL_HOLD_SECONDS

    for following in segments[index + 1 :]:
        if following.get("skip_subtitle"):
            continue
        next_start = max(
            0.0,
            float(following.get("tts_start", following.get("start", end))),
        )
        if next_start > start:
            end = min(end, max(base_end, next_start - SUBTITLE_TRANSITION_GAP_SECONDS))
        break

    return start, end
