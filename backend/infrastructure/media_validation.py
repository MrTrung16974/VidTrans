from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class InvalidVideoError(ValueError):
    pass


@dataclass(frozen=True)
class VideoProbe:
    duration: float
    width: int
    height: int


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def validate_video_file(
    path: Path,
    *,
    ffprobe: str = "ffprobe",
    runner: CommandRunner = subprocess.run,
) -> VideoProbe:
    """Reject empty, truncated and audio-only uploads before expensive processing."""

    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise InvalidVideoError("Video nguồn rỗng hoặc tải lên chưa hoàn tất. Hãy chọn lại file video.")
    try:
        result = runner(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,duration:format=duration",
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
        if not streams:
            raise InvalidVideoError("File không chứa luồng hình ảnh video.")
        stream = streams[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        duration_value = stream.get("duration") or (payload.get("format") or {}).get("duration")
        duration = float(duration_value or 0)
        if width < 1 or height < 1 or duration <= 0:
            raise InvalidVideoError("Không đọc được hình ảnh hoặc thời lượng của video nguồn.")
        return VideoProbe(duration=duration, width=width, height=height)
    except InvalidVideoError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError, subprocess.SubprocessError, OSError) as exc:
        raise InvalidVideoError(
            "Video nguồn bị hỏng, sai định dạng hoặc chưa tải đầy đủ. Hãy phát thử file rồi tải lại."
        ) from exc


def public_error_message(error: BaseException | str, *, max_length: int = 420) -> str:
    """Convert internal FFmpeg/provider failures into concise dashboard text."""

    raw = " ".join(str(error).split()).strip()
    lowered = raw.lower()
    invalid_media_markers = (
        "failed to load audio",
        "moov atom not found",
        "invalid data found when processing input",
        "error opening input file",
        "file does not exist",
        "uploaded source video is no longer available",
    )
    if any(marker in lowered for marker in invalid_media_markers):
        return "Video nguồn bị rỗng, hỏng hoặc chưa tải đầy đủ. Hãy chọn lại video rồi tạo job mới."
    if not raw:
        return "Xử lý video thất bại do lỗi không xác định."
    if len(raw) <= max_length:
        return raw
    return f"{raw[: max_length - 1].rstrip()}…"
