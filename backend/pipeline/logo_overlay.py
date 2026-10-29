"""Place the account logo on a video and render it with FFmpeg.

``plan_placements`` turns a detection (or none) into pixel rectangles over time;
``build_ffmpeg_command`` renders them in one pass: optional delogo over the old
logo, the new logo scaled to a share of the video width (never distorted) with
the configured opacity, audio kept as-is when the container allows it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from pipeline.logo_detection import Box, Segment

# The logo never grows taller than this share of the frame (tall logos on 16:9 video).
MAX_LOGO_HEIGHT = 0.3


@dataclass(frozen=True)
class Placement:
    start: float
    end: float
    x: int
    y: int
    width: int
    height: int
    source: str  # "detected" | "default" | "manual"
    # Pixel rectangle of the old logo to blur first, when cover_original is on.
    cover: tuple[int, int, int, int] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def logo_size(video_w: int, video_h: int, logo_w: int, logo_h: int, size_percent: float) -> tuple[int, int]:
    """Logo size in pixels: a share of the video width, keeping the logo's aspect."""
    width = video_w * size_percent / 100
    height = width * logo_h / max(1, logo_w)
    if height > video_h * MAX_LOGO_HEIGHT:
        scale = video_h * MAX_LOGO_HEIGHT / height
        width, height = width * scale, height * scale
    return _even(width), _even(height)


def corner_xy(position: str, video_w: int, video_h: int, width: int, height: int, margin: int) -> tuple[int, int]:
    x = margin if position.endswith("left") else video_w - width - margin
    y = margin if position.startswith("top") else video_h - height - margin
    return max(0, x), max(0, y)


def _cover_rect(box: Box, video_w: int, video_h: int) -> tuple[int, int, int, int] | None:
    # delogo needs the rectangle strictly inside the frame.
    pad = 4
    x0 = max(1, int(box.x * video_w) - pad)
    y0 = max(1, int(box.y * video_h) - pad)
    x1 = min(video_w - 2, int((box.x + box.w) * video_w) + pad)
    y1 = min(video_h - 2, int((box.y + box.h) * video_h) + pad)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return x0, y0, x1 - x0, y1 - y0


def plan_placements(
    segments: Sequence[Segment] | None,
    *,
    duration: float,
    video_w: int,
    video_h: int,
    logo_w: int,
    logo_h: int,
    size_percent: float,
    margin_percent: float,
    default_position: str,
    cover_original: bool = False,
    override: str = "auto",
) -> list[Placement]:
    """Cover the whole video with placements; segments without a logo use the default corner."""
    width, height = logo_size(video_w, video_h, logo_w, logo_h, size_percent)
    margin = int(round(min(video_w, video_h) * margin_percent / 100))
    if override != "auto":
        x, y = corner_xy(override, video_w, video_h, width, height, margin)
        return [Placement(0.0, duration, x, y, width, height, "manual")]
    default_x, default_y = corner_xy(default_position, video_w, video_h, width, height, margin)
    placements: list[Placement] = []
    for segment in segments or [Segment(0.0, duration, None, 0.0)]:
        if segment.box is None:
            placement = Placement(segment.start, segment.end, default_x, default_y, width, height, "default")
        else:
            cx, cy = segment.box.center
            # Centred on the old logo, but kept inside the margin like the default corner.
            x = min(max(margin, int(round(cx * video_w - width / 2))), max(0, video_w - width - margin))
            y = min(max(margin, int(round(cy * video_h - height / 2))), max(0, video_h - height - margin))
            cover = _cover_rect(segment.box, video_w, video_h) if cover_original else None
            placement = Placement(segment.start, segment.end, x, y, width, height, "detected", cover)
        previous = placements[-1] if placements else None
        if previous and (previous.x, previous.y, previous.cover) == (placement.x, placement.y, placement.cover):
            placements[-1] = Placement(previous.start, placement.end, previous.x, previous.y, width, height,
                                       previous.source, previous.cover)
        else:
            placements.append(placement)
    return placements


def _enable(placement: Placement, last: bool) -> str:
    if last:
        return f"gte(t\\,{placement.start:.3f})"
    return f"gte(t\\,{placement.start:.3f})*lt(t\\,{placement.end:.3f})"


def build_filter_graph(placements: Sequence[Placement], opacity: float) -> str:
    """Filter graph for inputs [0:v] (video) and [1:v] (logo image); output label [vout]."""
    single = len(placements) == 1
    width, height = placements[0].width, placements[0].height
    parts = [f"[1:v]scale={width}:{height}:flags=lanczos,format=rgba,colorchannelmixer=aa={opacity:.3f}[logo]"]
    logo_labels = ["[logo]"]
    if not single:
        logo_labels = [f"[logo{index}]" for index in range(len(placements))]
        parts.append(f"[logo]split={len(placements)}{''.join(logo_labels)}")
    current = "[0:v]"
    covers = [(p, index) for index, p in enumerate(placements) if p.cover]
    if covers:
        chain = []
        for placement, index in covers:
            x, y, w, h = placement.cover
            enable = "" if single else f":enable='{_enable(placement, index == len(placements) - 1)}'"
            chain.append(f"delogo=x={x}:y={y}:w={w}:h={h}{enable}")
        parts.append(f"{current}{','.join(chain)}[clean]")
        current = "[clean]"
    for index, placement in enumerate(placements):
        label = "[vout]" if index == len(placements) - 1 else f"[v{index}]"
        enable = "" if single else f":enable='{_enable(placement, index == len(placements) - 1)}'"
        parts.append(f"{current}{logo_labels[index]}overlay={placement.x}:{placement.y}{enable}{label}")
        current = label
    return ";".join(parts)


def build_ffmpeg_command(
    ffmpeg: str,
    video_path: Path,
    logo_path: Path,
    output_path: Path,
    placements: Sequence[Placement],
    *,
    opacity: float,
    copy_audio: bool = True,
) -> list[str]:
    audio = ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", "192k"]
    return [
        # A still image is one frame; overlay repeats it to the end (eof_action=repeat).
        ffmpeg, "-y", "-v", "error", "-i", str(video_path), "-i", str(logo_path),
        "-filter_complex", build_filter_graph(placements, opacity) + ";[vout]format=yuv420p[final]",
        "-map", "[final]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", *audio,
        "-movflags", "+faststart", str(output_path),
    ]
