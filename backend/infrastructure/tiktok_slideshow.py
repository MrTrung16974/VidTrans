"""Turn a TikTok photo post (slideshow) into an MP4 the translation pipeline can read.

A photo post has no video file: only its images and the soundtrack. Each image is
shown for a fixed time on a 1080x1920 canvas (fitted, over a blurred copy of itself
so nothing is cropped), with the soundtrack looped to the slideshow length.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

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
