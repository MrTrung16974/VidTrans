from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class MediaProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaProbe:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _positive_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _parse_fps(value: object) -> float:
    raw = str(value or "").strip()
    try:
        if "/" in raw:
            numerator, denominator = raw.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value:
                parsed = float(numerator) / denominator_value
            else:
                parsed = 0.0
        else:
            parsed = float(raw)
    except (TypeError, ValueError):
        parsed = 0.0
    return parsed if math.isfinite(parsed) and parsed > 0 else 30.0


def probe_media_file(
    path: Path,
    *,
    ffprobe: str = "ffprobe",
    runner: CommandRunner = subprocess.run,
) -> MediaProbe:
    """Read source video dimensions, duration, FPS and audio presence in one probe."""

    try:
        result = runner(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,width,height,duration,r_frame_rate:format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") or []
        video_stream = next(
            (stream for stream in streams if stream.get("codec_type") == "video"),
            None,
        )
        if not isinstance(video_stream, dict):
            raise MediaProbeError("Media source does not contain a video stream")
        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
        duration = _positive_float(video_stream.get("duration")) or _positive_float(
            (payload.get("format") or {}).get("duration")
        )
        if width < 1 or height < 1 or duration is None:
            raise MediaProbeError("Media source has invalid video metadata")
        return MediaProbe(
            duration=duration,
            width=width,
            height=height,
            fps=_parse_fps(video_stream.get("r_frame_rate")),
            has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
        )
    except MediaProbeError:
        raise
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
        raise MediaProbeError("Unable to read source media metadata") from exc
