"""H.264 encoder settings for rendered videos, picked from the hardware at startup.

``VIDTRANS_RENDER_ENCODER`` (auto | x264 | nvenc) and ``VIDTRANS_RENDER_PRESET``
(auto | an x264 preset) override the choice. On ``auto`` an NVIDIA GPU with an
NVENC-capable ffmpeg encodes on the GPU; otherwise x264 trades a little file size
for speed on small CPUs and keeps the denser preset once there are cores to spare.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

X264_PRESETS = {
    "ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow",
}
# Below this many cores the render competes with Whisper and TTS for the CPU.
MANY_CORES = 8


def nvenc_available(ffmpeg: str, device: Path = Path("/dev/nvidia0")) -> bool:
    if not device.exists():
        return False
    try:
        encoders = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=15, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "h264_nvenc" in encoders


@dataclass(frozen=True)
class VideoEncoder:
    codec: str = "libx264"
    preset: str = "veryfast"

    @classmethod
    def from_env(cls, ffmpeg: str, cpu_count: int, *, has_nvenc=nvenc_available) -> "VideoEncoder":
        choice = os.environ.get("VIDTRANS_RENDER_ENCODER", "auto").strip().lower() or "auto"
        if choice not in {"auto", "x264", "nvenc"}:
            raise ValueError("VIDTRANS_RENDER_ENCODER must be auto, x264 or nvenc")
        if choice == "nvenc" or (choice == "auto" and has_nvenc(ffmpeg)):
            encoder = cls("h264_nvenc", "p4")
        else:
            preset = os.environ.get("VIDTRANS_RENDER_PRESET", "auto").strip().lower() or "auto"
            if preset == "auto":
                preset = "veryfast" if cpu_count >= MANY_CORES else "superfast"
            if preset not in X264_PRESETS:
                raise ValueError(f"VIDTRANS_RENDER_PRESET must be auto or one of {sorted(X264_PRESETS)}")
            encoder = cls("libx264", preset)
        logger.info("Video encoder: %s (%s)", encoder.codec, encoder.preset)
        return encoder

    def args(self, quality: int) -> list[str]:
        """ffmpeg output options; ``quality`` is the x264 CRF, mapped to NVENC's constant quality."""
        if self.codec == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", self.preset, "-rc", "vbr", "-cq", str(quality), "-b:v", "0"]
        return ["-c:v", "libx264", "-preset", self.preset, "-crf", str(quality)]
