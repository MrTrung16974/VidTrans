"""Cut a finished (translated or dubbed) video into several clips.

The whole video is processed once, so every clip keeps the same voices and
subtitles. A plan is a list of ``ClipSpec``; cut points are moved to the gaps
between subtitle cues so no clip starts or ends in the middle of a sentence or
a dubbed line. Each clip is re-encoded (an exact cut, not the nearest keyframe)
in its original frame or as a 9:16 video for TikTok/Reels/Shorts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

MIN_CLIP_SECONDS = 5.0
MAX_CLIP_SECONDS = 10 * 60.0
MAX_CLIPS = 30
MAX_TITLE_CHARS = 120
# A cut point moves to a cue gap at most this far away; past it the exact time is kept.
SNAP_WINDOW_SECONDS = 4.0
# Equal splits may stretch or shrink a clip by this share to end between two cues.
EQUAL_SPLIT_TOLERANCE = 0.25

FRAME_ORIGINAL = "original"
# The whole frame, centred over a blurred, zoomed copy of itself: nothing is cropped,
# so burned subtitles and the logo stay readable.
FRAME_VERTICAL_BLUR = "vertical_blur"
# The middle of the frame, cropped to 9:16 and filling the screen. Burned subtitles would be
# cut off at the sides, so the clip is cut from the clean source and its subtitles are drawn
# again for the narrow frame (the voices come from the finished video).
FRAME_VERTICAL_CROP = "vertical_crop"
FRAMES = (FRAME_ORIGINAL, FRAME_VERTICAL_BLUR, FRAME_VERTICAL_CROP)
VERTICAL_WIDTH, VERTICAL_HEIGHT = 1080, 1920
# Subtitle size on the 9:16 frame: the source size scaled to 1920 px tall, shrunk so a line
# holds about 20-26 characters across 1080 px, within readable bounds.
VERTICAL_FONT_SHRINK = 0.7
VERTICAL_FONT_MIN, VERTICAL_FONT_MAX = 44, 80
VERTICAL_TEXT_WIDTH = 0.86 * VERTICAL_WIDTH


class ClipPlanError(ValueError):
    """A plan the user must fix; the message is shown as is (Vietnamese)."""


@dataclass(frozen=True)
class ClipSpec:
    start: float
    end: float
    title: str = ""
    frame: str = FRAME_ORIGINAL

    @property
    def duration(self) -> float:
        return self.end - self.start

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "title": self.title, "frame": self.frame,
                "duration": round(self.duration, 3)}


def _cue_times(segments: Iterable[dict[str, Any]]) -> list[tuple[float, float]]:
    cues = []
    for segment in segments:
        try:
            start, end = float(segment["start"]), float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(start) and math.isfinite(end) and end > start:
            cues.append((start, end))
    return sorted(cues)


def cut_points(segments: Sequence[dict[str, Any]], duration: float) -> list[float]:
    """Times where a cut splits no cue: the video ends and the middle of every gap between cues."""
    points = {0.0, round(duration, 3)}
    cues = _cue_times(segments)
    covered_until = 0.0
    for index, (start, end) in enumerate(cues):
        if index and start >= covered_until:
            points.add(round((covered_until + start) / 2, 3))
        covered_until = max(covered_until, end)
    if cues and covered_until < duration:
        # Just after the last line, so a clip need not run on through a silent ending.
        points.add(round(min(duration, covered_until + 0.3), 3))
    return sorted(point for point in points if 0 <= point <= duration)


def snap_time(time: float, points: Sequence[float], *, window: float = SNAP_WINDOW_SECONDS) -> float:
    """The cut point nearest ``time`` when one is within ``window`` seconds, else ``time``."""
    if not points:
        return time
    nearest = min(points, key=lambda point: abs(point - time))
    return nearest if abs(nearest - time) <= window else time


def plan_equal_clips(
    segments: Sequence[dict[str, Any]], duration: float, clip_seconds: float,
) -> list[ClipSpec]:
    """Back-to-back clips of about ``clip_seconds``, each ending between two cues."""
    if not MIN_CLIP_SECONDS <= clip_seconds <= MAX_CLIP_SECONDS:
        raise ClipPlanError(
            f"Độ dài mỗi clip phải từ {MIN_CLIP_SECONDS:g} giây đến {MAX_CLIP_SECONDS / 60:g} phút"
        )
    if duration < MIN_CLIP_SECONDS:
        raise ClipPlanError(f"Video ngắn hơn {MIN_CLIP_SECONDS:g} giây, không cắt được")
    if math.ceil(duration / clip_seconds) > MAX_CLIPS * 2:
        raise ClipPlanError(f"Độ dài clip quá ngắn so với video: tối đa {MAX_CLIPS} clip mỗi lần cắt")
    points = cut_points(segments, duration)
    low, high = clip_seconds * (1 - EQUAL_SPLIT_TOLERANCE), clip_seconds * (1 + EQUAL_SPLIT_TOLERANCE)
    bounds = [0.0]
    while duration - bounds[-1] > high:
        start = bounds[-1]
        target = start + clip_seconds
        candidates = [point for point in points if start + low <= point <= start + high]
        bounds.append(min(candidates, key=lambda point: abs(point - target)) if candidates else round(target, 3))
    bounds.append(round(duration, 3))
    # A short tail joins the clip before it.
    if len(bounds) > 2 and bounds[-1] - bounds[-2] < max(MIN_CLIP_SECONDS, low):
        del bounds[-2]
    clips = [ClipSpec(start, end, f"Phần {number}") for number, (start, end)
             in enumerate(zip(bounds, bounds[1:]), start=1)]
    if len(clips) > MAX_CLIPS:
        raise ClipPlanError(f"Chia ra {len(clips)} clip, quá giới hạn {MAX_CLIPS} clip mỗi lần cắt")
    return clips


def validate_plan(
    raw_clips: Sequence[dict[str, Any]],
    duration: float,
    *,
    segments: Sequence[dict[str, Any]] = (),
    snap: bool = True,
) -> list[ClipSpec]:
    """Clean a user's plan: snapped to cue gaps, inside the video, long enough, not overlapping."""
    if not raw_clips:
        raise ClipPlanError("Plan cắt chưa có clip nào")
    if len(raw_clips) > MAX_CLIPS:
        raise ClipPlanError(f"Tối đa {MAX_CLIPS} clip mỗi lần cắt")
    points = cut_points(segments, duration) if snap else []
    clips: list[ClipSpec] = []
    for number, raw in enumerate(raw_clips, start=1):
        try:
            start, end = float(raw["start"]), float(raw["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ClipPlanError(f"Clip {number}: thiếu hoặc sai thời điểm bắt đầu/kết thúc") from exc
        if not (math.isfinite(start) and math.isfinite(end)):
            raise ClipPlanError(f"Clip {number}: thời điểm không hợp lệ")
        if snap:
            start, end = snap_time(start, points), snap_time(end, points)
        start, end = max(0.0, round(start, 3)), min(round(duration, 3), round(end, 3))
        if end - start < MIN_CLIP_SECONDS:
            raise ClipPlanError(f"Clip {number} ngắn hơn {MIN_CLIP_SECONDS:g} giây (sau khi khớp ranh giới câu)")
        if end - start > MAX_CLIP_SECONDS:
            raise ClipPlanError(f"Clip {number} dài hơn {MAX_CLIP_SECONDS / 60:g} phút")
        frame = str(raw.get("frame") or FRAME_ORIGINAL)
        if frame not in FRAMES:
            raise ClipPlanError(f"Clip {number}: khung hình không hợp lệ")
        title = " ".join(str(raw.get("title") or "").split())[:MAX_TITLE_CHARS] or f"Phần {number}"
        clips.append(ClipSpec(start, end, title, frame))
    ordered = sorted(clips, key=lambda clip: clip.start)
    for before, after in zip(ordered, ordered[1:]):
        if after.start < before.end - 0.05:
            raise ClipPlanError(f"Clip “{before.title}” và “{after.title}” bị chồng lên nhau")
    return clips


def slice_segments(segments: Sequence[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    """The cues inside [start, end], shifted to the clip's own timeline."""
    sliced = []
    for segment in segments:
        try:
            cue_start, cue_end = float(segment["start"]), float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        overlap = min(cue_end, end) - max(cue_start, start)
        # A cue barely touching the edge belongs to the neighbouring clip.
        if overlap <= 0 or (overlap < 0.5 * (cue_end - cue_start) and overlap < 0.3):
            continue
        sliced.append({
            **segment,
            "start": round(max(cue_start, start) - start, 3),
            "end": round(min(cue_end, end) - start, 3),
        })
    return sliced


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_srt(segments: Sequence[dict[str, Any]]) -> str:
    blocks = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text or segment.get("skip_subtitle"):
            continue
        blocks.append(f"{len(blocks) + 1}\n{_srt_time(float(segment['start']))} --> "
                      f"{_srt_time(float(segment['end']))}\n{text}\n")
    return "\n".join(blocks)


@dataclass(frozen=True)
class CropBox:
    """The 9:16 window of a ``source_width`` x ``source_height`` frame, and its scale to 1080x1920."""
    width: int
    height: int
    x: int
    y: int

    @property
    def scale(self) -> float:
        return VERTICAL_HEIGHT / self.height

    @property
    def filter(self) -> str:
        return (f"crop={self.width}:{self.height}:{self.x}:{self.y},"
                f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT},setsar=1")


def vertical_crop_box(source_width: int, source_height: int) -> CropBox:
    """The centred 9:16 window: full height of a wide frame, full width of a tall one."""
    if source_width <= 0 or source_height <= 0:
        raise ValueError("Video size must be positive")
    if source_width * 16 >= source_height * 9:
        width = min(source_width, int(round(source_height * 9 / 16 / 2)) * 2)
        return CropBox(width, source_height, (source_width - width) // 2, 0)
    height = min(source_height, int(round(source_width * 16 / 9 / 2)) * 2)
    return CropBox(source_width, height, 0, (source_height - height) // 2)


def _wrap(text: str, width: int) -> str:
    lines: list[str] = []
    for word in " ".join(text.split()).split():
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return "\n".join(lines)


def vertical_segments(
    segments: Sequence[dict[str, Any]], box: CropBox, *, base_font_size: float,
) -> list[dict[str, Any]]:
    """Subtitle cues laid out again for the 9:16 frame cut out by ``box``.

    Each line is centred across the narrow frame at the height the source subtitle had, wrapped
    for its width, and the patch hiding the source's own burned subtitle moves with the crop.
    """
    scale = box.scale
    laid_out = []
    for segment in segments:
        layout = segment.get("subtitle_layout") if isinstance(segment.get("subtitle_layout"), dict) else {}
        source_size = float(layout.get("font_size") or base_font_size)
        font_size = int(min(VERTICAL_FONT_MAX, max(VERTICAL_FONT_MIN, round(source_size * scale * VERTICAL_FONT_SHRINK))))
        if layout.get("y") is not None:
            y = (float(layout["y"]) - box.y) * scale
        else:
            y = VERTICAL_HEIGHT * 0.78  # Above the captions and buttons of TikTok/Reels/Shorts.
        y = min(VERTICAL_HEIGHT - font_size, max(font_size, y))
        chars_per_line = max(12, int(VERTICAL_TEXT_WIDTH / (font_size * 0.56)))
        new_layout: dict[str, Any] = {
            "x": VERTICAL_WIDTH / 2, "y": round(y, 1), "font_size": font_size,
            "render_text": _wrap(str(segment.get("text") or ""), chars_per_line),
        }
        mask = layout.get("mask") if isinstance(layout.get("mask"), dict) else None
        if layout.get("mask_original") and mask:
            left = max(0.0, (float(mask["x"]) - box.x) * scale)
            top = max(0.0, (float(mask["y"]) - box.y) * scale)
            right = min(float(VERTICAL_WIDTH), (float(mask["x"]) + float(mask["width"]) - box.x) * scale)
            bottom = min(float(VERTICAL_HEIGHT), (float(mask["y"]) + float(mask["height"]) - box.y) * scale)
            if right - left >= 2 and bottom - top >= 2:
                new_layout.update(mask_original=True, mask={
                    "x": round(left, 1), "y": round(top, 1),
                    "width": round(right - left, 1), "height": round(bottom - top, 1),
                })
        laid_out.append({**segment, "subtitle_layout": new_layout})
    return laid_out


def build_crop_source_command(ffmpeg: str, source: Path, clip: ClipSpec, box: CropBox, output: Path) -> list[str]:
    """The clip's span of the clean source, cropped to 9:16, without sound (added at the subtitle render)."""
    return [
        ffmpeg, "-y", "-v", "error",
        "-ss", f"{clip.start:.3f}", "-i", str(source), "-t", f"{clip.duration:.3f}",
        "-vf", box.filter, "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
        str(output),
    ]


def build_audio_cut_command(ffmpeg: str, source: Path, clip: ClipSpec, output: Path) -> list[str]:
    """The clip's span of the finished video's sound track (the dub and music as mixed)."""
    return [
        ffmpeg, "-y", "-v", "error",
        "-ss", f"{clip.start:.3f}", "-i", str(source), "-t", f"{clip.duration:.3f}",
        "-vn", "-map", "0:a:0", "-c:a", "pcm_s16le", str(output),
    ]


def build_clip_command(ffmpeg: str, source: Path, clip: ClipSpec, output: Path,
                       *, source_size: tuple[int, int] | None = None) -> list[str]:
    """An exact, re-encoded cut of ``source`` in the clip's frame.

    ``vertical_crop`` here crops the finished video as is; that is only right when it has no
    burned subtitles (a video without translation). Otherwise the clip job renders it again.
    """
    command = [
        ffmpeg, "-y", "-v", "error",
        "-ss", f"{clip.start:.3f}", "-i", str(source), "-t", f"{clip.duration:.3f}",
    ]
    if clip.frame == FRAME_VERTICAL_BLUR:
        width, height = VERTICAL_WIDTH, VERTICAL_HEIGHT
        command += [
            "-filter_complex",
            f"[0:v]split[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"boxblur=24:2[blur];"
            f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fit];"
            f"[blur][fit]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[video]",
            "-map", "[video]",
        ]
    elif clip.frame == FRAME_VERTICAL_CROP:
        if source_size is None:
            raise ValueError("Cropping needs the source video size")
        command += ["-vf", vertical_crop_box(*source_size).filter, "-map", "0:v:0"]
    else:
        command += ["-map", "0:v:0"]
    command += [
        "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output),
    ]
    return command
