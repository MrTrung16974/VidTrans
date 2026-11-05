"""Turn a TikTok photo post (slideshow) into an MP4 the translation pipeline can read.

A photo post has no video file: only its images and the soundtrack. Each image is
shown for a fixed time on a 1080x1920 canvas (fitted, over a blurred copy of itself
so nothing is cropped), with the soundtrack looped to the slideshow length.
"""

from __future__ import annotations

import logging
import math
import subprocess
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger(__name__)

WIDTH, HEIGHT, FPS = 1080, 1920, 30
SECONDS_PER_IMAGE = 3.0
MAX_IMAGES = 35  # TikTok's own limit for a photo post


def slideshow_duration(image_count: int, seconds_per_image: float = SECONDS_PER_IMAGE) -> float:
    return max(1, image_count) * seconds_per_image


def build_slideshow_command(
    ffmpeg: str,
    images: Sequence[Path],
    audio: Path | None,
    output: Path,
    *,
    seconds_per_image: float = SECONDS_PER_IMAGE,
) -> list[str]:
    if not images:
        raise ValueError("A slideshow needs at least one image")
    total = slideshow_duration(len(images), seconds_per_image)
    command = [ffmpeg, "-y", "-v", "error"]
    for image in images:
        command += ["-loop", "1", "-framerate", str(FPS), "-t", f"{seconds_per_image:.3f}", "-i", str(image)]
    if audio is not None:
        command += ["-stream_loop", "-1", "-i", str(audio)]
    else:
        # The pipeline expects an audio track; give it silence.
        command += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
    audio_index = len(images)

    parts = []
    for index in range(len(images)):
        parts.append(
            f"[{index}:v]split[bg{index}][fg{index}];"
            f"[bg{index}]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},boxblur=24:2[blur{index}];"
            f"[fg{index}]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease[fit{index}];"
            f"[blur{index}][fit{index}]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS},format=yuv420p[v{index}]"
        )
    concat_inputs = "".join(f"[v{index}]" for index in range(len(images)))
    parts.append(f"{concat_inputs}concat=n={len(images)}:v=1:a=0[video]")
    fade_start = max(0.0, total - 1.0)
    parts.append(f"[{audio_index}:a]afade=t=out:st={fade_start:.3f}:d=1,aresample=44100[audio]")

    command += [
        "-filter_complex", ";".join(parts),
        "-map", "[video]", "-map", "[audio]",
        "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(output),
    ]
    return command


def render_slideshow(
    ffmpeg: str,
    images: Sequence[Path],
    audio: Path | None,
    output: Path,
    *,
    seconds_per_image: float = SECONDS_PER_IMAGE,
    timeout: float = 600,
) -> float:
    """Render ``output`` and return its duration in seconds."""
    command = build_slideshow_command(ffmpeg, images, audio, output, seconds_per_image=seconds_per_image)
    subprocess.run(command, check=True, capture_output=True, timeout=timeout)
    return slideshow_duration(len(images), seconds_per_image)


# A photo post turned into a video of a chosen length: each image stays on screen
# at most this long with a slow zoom, and the whole set repeats when the chosen
# length needs more than one pass.
MAX_SECONDS_PER_SLIDE = 8.0
MIN_SECONDS_PER_SLIDE = 1.0
MIN_VIDEO_SECONDS = 5.0
MAX_VIDEO_SECONDS = 10 * 60.0
ZOOM_AMOUNT = 0.06
# Slides are composed larger than the output so the zoom has pixels to spare
# and does not shimmer from rounding the crop to whole output pixels.
COMPOSE_SCALE = 1.5


def plan_timed_slideshow(
    image_count: int,
    total_seconds: float,
    *,
    max_seconds_per_slide: float = MAX_SECONDS_PER_SLIDE,
) -> tuple[list[int], float]:
    """Return (image index per slide, seconds per slide) filling ``total_seconds``.

    Images share the time evenly; when that would hold each one longer than
    ``max_seconds_per_slide`` the set is repeated in order as often as needed.
    """
    if image_count < 1:
        raise ValueError("A slideshow needs at least one image")
    if total_seconds <= 0:
        raise ValueError("A slideshow needs a positive length")
    passes = max(1, math.ceil(total_seconds / (image_count * max_seconds_per_slide)))
    order = [index for _ in range(passes) for index in range(image_count)]
    seconds = total_seconds / len(order)
    if seconds < MIN_SECONDS_PER_SLIDE:
        # More images than the length allows: keep the first ones that fit.
        order = order[: max(1, int(total_seconds // MIN_SECONDS_PER_SLIDE))]
        seconds = total_seconds / len(order)
    return order, seconds


def compose_slide(image: Path, output: Path, *, scale: float = COMPOSE_SCALE) -> Path:
    """Write ``image`` fitted on a portrait canvas over a blurred copy of itself."""
    from PIL import Image, ImageFilter, ImageOps

    width, height = round(WIDTH * scale), round(HEIGHT * scale)
    with Image.open(image) as opened:
        picture = ImageOps.exif_transpose(opened).convert("RGB")
    background = ImageOps.fit(picture, (width, height), Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(radius=round(36 * scale)))
    foreground = ImageOps.contain(picture, (width, height), Image.Resampling.LANCZOS)
    background.paste(foreground, ((width - foreground.width) // 2, (height - foreground.height) // 2))
    output.parent.mkdir(parents=True, exist_ok=True)
    background.save(output, "JPEG", quality=92)
    return output


def build_timed_slideshow_command(
    ffmpeg: str,
    slides: Sequence[Path],
    seconds_per_slide: float,
    audio: Path | None,
    output: Path,
    *,
    list_file: Path,
) -> list[str]:
    """ffmpeg command for ``slides`` (composed frames), each zoomed in for ``seconds_per_slide``."""
    if not slides:
        raise ValueError("A slideshow needs at least one slide")
    frames = max(1, round(seconds_per_slide * FPS))
    total = len(slides) * frames / FPS
    # zoompan turns each input picture into `frames` output frames, whatever its timestamp.
    list_file.write_text("".join(f"file '{slide.resolve().as_posix()}'\n" for slide in slides), encoding="utf-8")
    command = [ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(list_file)]
    if audio is not None:
        command += ["-stream_loop", "-1", "-i", str(audio)]
    else:
        command += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
    zoom = f"1+{ZOOM_AMOUNT}*mod(on,{frames})/{frames}"
    video = (
        f"[0:v]zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},setsar=1,format=yuv420p[video]"
    )
    fade = min(2.0, total / 4)
    sound = f"[1:a]afade=t=out:st={max(0.0, total - fade):.3f}:d={fade:.3f},aresample=44100[audio]"
    command += [
        "-filter_complex", f"{video};{sound}",
        "-map", "[video]", "-map", "[audio]",
        "-t", f"{total:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(output),
    ]
    return command


MAX_MUSIC_TRACKS = 10


def build_audio_playlist_command(ffmpeg: str, tracks: Sequence[Path], output: Path) -> list[str]:
    """ffmpeg command joining ``tracks`` one after another into ``output`` (AAC)."""
    if not tracks:
        raise ValueError("A playlist needs at least one track")
    command = [ffmpeg, "-y", "-v", "error"]
    for track in tracks:
        command += ["-i", str(track)]
    # Tracks differ in rate and layout; bring them to one format before concat.
    parts = [
        f"[{index}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
        for index in range(len(tracks))
    ]
    inputs = "".join(f"[a{index}]" for index in range(len(tracks)))
    parts.append(f"{inputs}concat=n={len(tracks)}:v=0:a=1[audio]")
    command += [
        "-filter_complex", ";".join(parts),
        "-map", "[audio]", "-vn",
        "-c:a", "aac", "-b:a", "192k",
        str(output),
    ]
    return command


def join_audio_tracks(ffmpeg: str, tracks: Sequence[Path], output: Path, *, timeout: float = 600) -> Path:
    """Return one soundtrack playing ``tracks`` in order (the single track itself when only one)."""
    if len(tracks) == 1:
        return tracks[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(build_audio_playlist_command(ffmpeg, tracks, output), check=True, capture_output=True,
                   timeout=timeout)
    return output


def render_timed_slideshow(
    ffmpeg: str,
    images: Sequence[Path],
    audio: Path | None,
    output: Path,
    total_seconds: float,
    *,
    work_dir: Path,
    timeout: float = 3600,
) -> float:
    """Render a ``total_seconds`` video from ``images`` with ``audio`` looped; return its length.

    An image that cannot be decoded (HEIC without a codec, a broken download)
    is left out; with none left a ValueError says so.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    composed: list[Path] = []
    for index, image in enumerate(images):
        try:
            composed.append(compose_slide(image, work_dir / f"slide_{index:03d}.jpg"))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable slideshow image %s: %s", image.name, exc)
    if not composed:
        raise ValueError("Không đọc được ảnh nào của bài đăng để dựng video")
    order, seconds = plan_timed_slideshow(len(composed), total_seconds)
    slides = [composed[index] for index in order]
    command = build_timed_slideshow_command(
        ffmpeg, slides, seconds, audio, output, list_file=work_dir / "slides.txt"
    )
    subprocess.run(command, check=True, capture_output=True, timeout=timeout)
    return len(slides) * max(1, round(seconds * FPS)) / FPS


def _planned_motion(motion: str, frames: int) -> tuple[str, str, str]:
    """Safe zoompan expressions for the slideshow-plan motion allowlist."""
    progress = f"min(on/{max(1, frames - 1)},1)"
    center_x, center_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "slow_zoom_out":
        return f"1+{ZOOM_AMOUNT}*(1-{progress})", center_x, center_y
    if motion == "pan_left":
        return f"1+{ZOOM_AMOUNT}", f"(iw-iw/zoom)*(1-{progress})", center_y
    if motion == "pan_right":
        return f"1+{ZOOM_AMOUNT}", f"(iw-iw/zoom)*{progress}", center_y
    if motion == "pan_up":
        return f"1+{ZOOM_AMOUNT}", center_x, f"(ih-ih/zoom)*(1-{progress})"
    if motion == "pan_down":
        return f"1+{ZOOM_AMOUNT}", center_x, f"(ih-ih/zoom)*{progress}"
    if motion == "static":
        return "1.001", center_x, center_y
    return f"1+{ZOOM_AMOUNT}*{progress}", center_x, center_y


def build_planned_slideshow_command(
    ffmpeg: str,
    slides: Sequence[tuple[Path, dict[str, Any]]],
    audio: Path | None,
    output: Path,
) -> list[str]:
    """Build an FFmpeg graph for validated per-slide timing, motion and transitions."""
    if not slides:
        raise ValueError("A slideshow plan needs at least one slide")
    total = sum(float(spec["duration_seconds"]) for _, spec in slides)
    command = [ffmpeg, "-y", "-v", "error"]
    effective_transitions: list[float] = []
    for index, (image, spec) in enumerate(slides):
        transition = 0.0 if index == len(slides) - 1 else float(spec.get("transition_seconds") or 0)
        effective = max(0.001, transition) if index < len(slides) - 1 else 0.0
        effective_transitions.append(effective)
        input_seconds = float(spec["duration_seconds"]) + effective
        command += ["-loop", "1", "-framerate", str(FPS), "-t", f"{input_seconds:.3f}", "-i", str(image)]
    if audio is not None:
        command += ["-stream_loop", "-1", "-i", str(audio)]
    else:
        command += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]

    parts: list[str] = []
    for index, (_, spec) in enumerate(slides):
        duration = float(spec["duration_seconds"]) + effective_transitions[index]
        frames = max(1, round(duration * FPS))
        zoom, x, y = _planned_motion(str(spec.get("motion") or "slow_zoom_in"), frames)
        parts.append(
            f"[{index}:v]zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:"
            f"s={WIDTH}x{HEIGHT}:fps={FPS},trim=duration={duration:.3f},"
            f"setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v{index}]"
        )
    video_label = "v0"
    offset = float(slides[0][1]["duration_seconds"])
    for index in range(1, len(slides)):
        previous = slides[index - 1][1]
        transition = effective_transitions[index - 1]
        transition_name = "fadeblack" if previous.get("transition") == "fade" else "fade"
        output_label = f"mix{index}"
        parts.append(
            f"[{video_label}][v{index}]xfade=transition={transition_name}:"
            f"duration={transition:.3f}:offset={offset:.3f}[{output_label}]"
        )
        video_label = output_label
        offset += float(slides[index][1]["duration_seconds"])
    audio_index = len(slides)
    fade = min(2.0, total / 4)
    parts.append(f"[{audio_index}:a]afade=t=out:st={max(0.0, total - fade):.3f}:d={fade:.3f},aresample=44100[audio]")
    command += [
        "-filter_complex", ";".join(parts),
        "-map", f"[{video_label}]", "-map", "[audio]",
        "-t", f"{total:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output),
    ]
    return command


def render_planned_slideshow(
    ffmpeg: str,
    images: Sequence[Path],
    plan: dict[str, Any],
    audio: Path | None,
    output: Path,
    *,
    work_dir: Path,
    timeout: float = 3600,
) -> float:
    """Render a server-validated slideshow plan and return its target duration."""
    work_dir.mkdir(parents=True, exist_ok=True)
    planned: list[tuple[Path, dict[str, Any]]] = []
    for position, spec in enumerate(plan.get("slides") or []):
        index = int(spec["image_index"])
        if index < 0 or index >= len(images):
            raise ValueError("Slideshow plan image index is out of range")
        composed = compose_slide(images[index], work_dir / f"planned_{position:03d}.jpg")
        planned.append((composed, spec))
    command = build_planned_slideshow_command(ffmpeg, planned, audio, output)
    subprocess.run(command, check=True, capture_output=True, timeout=timeout)
    return sum(float(spec["duration_seconds"]) for _, spec in planned)
