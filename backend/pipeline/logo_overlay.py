"""Place the account logo on a video and render it with FFmpeg.

``plan_placements`` turns a detection (or none) into pixel rectangles over time;
``build_ffmpeg_command`` renders them in one pass: optional delogo over the old
logo, the new logo scaled to a share of the video width (never distorted) with
the configured opacity, audio kept as-is when the container allows it.

A moving logo becomes one placement with keyframes; FFmpeg evaluates the overlay
position per frame as a flat sum of gated linear pieces (no nested ``if``), so the
expression stays small after the tracker reduced the path to a few keyframes.
When the old logo is inpainted (``logo_inpaint``), the video input is the repaired
raw frame stream instead of the file and ``delogo`` is not used.
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
    source: str  # "detected" | "tracked" | "default" | "manual"
    # Pixel rectangle of the old logo to blur first (delogo), when cover_original is on.
    cover: tuple[int, int, int, int] | None = None
    # Old logo to inpaint: its box (shares of the frame) and the detection stencil index.
    cover_box: tuple[float, float, float, float] | None = None
    cover_track: int | None = None
    # Moving logo: (t, x, y) pixel positions of the new logo's top-left corner.
    keyframes: tuple[tuple[float, int, int], ...] | None = None
    # Moving old logo: (t, centre x, centre y) shares, to move the inpainting mask with it.
    cover_keyframes: tuple[tuple[float, float, float], ...] | None = None

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
    cover_method: str = "blur",
    override: str = "auto",
) -> list[Placement]:
    """Cover the whole video with placements; segments without a logo use the default corner."""
    width, height = logo_size(video_w, video_h, logo_w, logo_h, size_percent)
    margin = int(round(min(video_w, video_h) * margin_percent / 100))
    if override != "auto":
        x, y = corner_xy(override, video_w, video_h, width, height, margin)
        return [Placement(0.0, duration, x, y, width, height, "manual")]
    default_x, default_y = corner_xy(default_position, video_w, video_h, width, height, margin)
    max_x, max_y = max(0, video_w - width - margin), max(0, video_h - height - margin)

    def anchor(cx: float, cy: float) -> tuple[int, int]:
        # Centred on the old logo, but kept inside the margin like the default corner.
        return (min(max(margin, int(round(cx * video_w - width / 2))), max_x),
                min(max(margin, int(round(cy * video_h - height / 2))), max_y))

    placements: list[Placement] = []
    for segment in segments or [Segment(0.0, duration, None, 0.0)]:
        if segment.box is None:
            placement = Placement(segment.start, segment.end, default_x, default_y, width, height, "default")
        else:
            x, y = anchor(*segment.box.center)
            moving = bool(segment.keyframes)
            fields: dict = {}
            if cover_original and cover_method == "inpaint":
                box = segment.box
                fields.update(cover_box=(box.x, box.y, box.w, box.h), cover_track=segment.track,
                              cover_keyframes=tuple(segment.keyframes) if moving else None)
            elif cover_original and not moving:
                # delogo cannot follow a moving logo; those are only covered by inpainting.
                fields["cover"] = _cover_rect(segment.box, video_w, video_h)
            if moving:
                fields["keyframes"] = tuple((t, *anchor(cx, cy)) for t, cx, cy in segment.keyframes)
            placement = Placement(segment.start, segment.end, x, y, width, height,
                                  "tracked" if moving else "detected", **fields)
        previous = placements[-1] if placements else None
        if previous and not previous.keyframes and not placement.keyframes and (
                (previous.x, previous.y, previous.cover, previous.cover_box)
                == (placement.x, placement.y, placement.cover, placement.cover_box)):
            placements[-1] = Placement(**{**asdict(previous), "end": placement.end})
        else:
            placements.append(placement)
    return placements


def _enable(placement: Placement, last: bool) -> str:
    if last:
        return f"gte(t\\,{placement.start:.3f})"
    return f"gte(t\\,{placement.start:.3f})*lt(t\\,{placement.end:.3f})"


def path_expression(keyframes: Sequence[tuple[float, float, float]], axis: int) -> str:
    """Piecewise-linear position over time: hold before the first and after the last keyframe."""
    values = [(t, point[axis]) for t, *point in keyframes]
    first_t, first_v = values[0]
    last_t, last_v = values[-1]
    terms = [f"lt(t\\,{first_t:.3f})*{first_v}"]
    for (t0, v0), (t1, v1) in zip(values, values[1:]):
        if t1 <= t0:
            continue
        slope = (v1 - v0) / (t1 - t0)
        terms.append(f"gte(t\\,{t0:.3f})*lt(t\\,{t1:.3f})*({v0}+{slope:.4f}*(t-{t0:.3f}))")
    terms.append(f"gte(t\\,{last_t:.3f})*{last_v}")
    return "+".join(terms)


def build_filter_graph(placements: Sequence[Placement], opacity: float, *,
                       video_label: str = "[0:v]", logo_label: str = "[1:v]", use_delogo: bool = True) -> str:
    """Filter graph from the video and logo inputs to the output label [vout]."""
    single = len(placements) == 1
    width, height = placements[0].width, placements[0].height
    parts = [f"{logo_label}scale={width}:{height}:flags=lanczos,format=rgba,colorchannelmixer=aa={opacity:.3f}[logo]"]
    logo_labels = ["[logo]"]
    if not single:
        logo_labels = [f"[logo{index}]" for index in range(len(placements))]
        parts.append(f"[logo]split={len(placements)}{''.join(logo_labels)}")
    current = video_label
    covers = [(p, index) for index, p in enumerate(placements) if p.cover] if use_delogo else []
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
        if placement.keyframes:
            position = (f"x='{path_expression(placement.keyframes, 0)}'"
                        f":y='{path_expression(placement.keyframes, 1)}'")
        else:
            position = f"{placement.x}:{placement.y}"
        parts.append(f"{current}{logo_labels[index]}overlay={position}{enable}{label}")
        current = label
    return ";".join(parts)


def _encode_args(copy_audio: bool, output_path: Path) -> list[str]:
    audio = ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", "192k"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", *audio,
            "-movflags", "+faststart", str(output_path)]


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
    return [
        # A still image is one frame; overlay repeats it to the end (eof_action=repeat).
        ffmpeg, "-y", "-v", "error", "-i", str(video_path), "-i", str(logo_path),
        "-filter_complex", build_filter_graph(placements, opacity) + ";[vout]format=yuv420p[final]",
        "-map", "[final]", "-map", "0:a?", *_encode_args(copy_audio, output_path),
    ]


def build_raw_input_command(
    ffmpeg: str,
    video_path: Path,
    logo_path: Path,
    output_path: Path,
    placements: Sequence[Placement],
    *,
    width: int,
    height: int,
    fps: float,
    opacity: float,
    copy_audio: bool = True,
) -> list[str]:
    """Encoder for repaired BGR frames on stdin; audio still comes from the source file."""
    return [
        ffmpeg, "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:.6f}", "-i", "pipe:0",
        "-i", str(video_path), "-i", str(logo_path),
        "-filter_complex",
        build_filter_graph(placements, opacity, video_label="[0:v]", logo_label="[2:v]", use_delogo=False)
        + ";[vout]format=yuv420p[final]",
        "-map", "[final]", "-map", "1:a?", *_encode_args(copy_audio, output_path),
    ]


def build_decode_command(ffmpeg: str, video_path: Path, fps: float) -> list[str]:
    """Constant-rate BGR frames, so frame index / fps is the timestamp the encoder will see."""
    return [ffmpeg, "-v", "error", "-i", str(video_path), "-map", "0:v:0", "-an", "-sn",
            "-vf", f"fps={fps:.6f}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
