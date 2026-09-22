from __future__ import annotations

import json
import math
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
        duration = 0.0
        for value in (stream.get("duration"), (payload.get("format") or {}).get("duration")):
            try:
                candidate = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(candidate) and candidate > 0:
                duration = candidate
                break
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
        "moov atom not found",
        "invalid data found when processing input",
    )
    if "uploaded source video is no longer available" in lowered:
        return "Không còn file video nguồn trên máy chủ để tiếp tục job. Hãy tải lại video và tạo job mới."
    if "permission denied" in lowered:
        return "Máy chủ không có quyền đọc hoặc ghi file xử lý. Kiểm tra quyền thư mục uploads, work và outputs."
    if "no space left on device" in lowered:
        return "Máy chủ đã hết dung lượng lưu trữ. Giải phóng dung lượng trước khi xử lý lại video."
    if "matches no streams" in lowered or "does not contain any stream" in lowered:
        return "Không tìm thấy luồng âm thanh hoặc hình ảnh cần xử lý. Kiểm tra âm thanh của video hoặc chọn OCR cho video không tiếng."
    if "no such file or directory" in lowered or "file does not exist" in lowered:
        return "Không tìm thấy file hoặc chương trình cần cho bước xử lý. Kiểm tra log máy chủ, file nguồn và cấu hình FFmpeg."
    if any(marker in lowered for marker in invalid_media_markers):
        return "Video nguồn bị rỗng, hỏng hoặc chưa tải đầy đủ. Hãy chọn lại video rồi tạo job mới."
    if "failed to load audio" in lowered:
        return "Không đọc được âm thanh của video. Kiểm tra luồng âm thanh và log FFmpeg trên máy chủ; chưa đủ thông tin để kết luận video bị hỏng."
    if not raw:
        return "Xử lý video thất bại do lỗi không xác định."
    if len(raw) <= max_length:
        return raw
    return f"{raw[: max_length - 1].rstrip()}…"
