from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppSettings:
    """Filesystem and executable configuration for one VidTrans deployment."""

    base_dir: Path
    frontend_dir: Path
    upload_dir: Path
    output_dir: Path
    work_dir: Path
    ffmpeg: str
    ffprobe: str
    paddle_ocr_enabled: bool
    worker_concurrency: int
    whisper_concurrency: int
    ocr_concurrency: int

    @classmethod
    def load(cls, base_dir: Path) -> "AppSettings":
        base_dir = Path(base_dir).resolve()
        # Keep native macOS development working without affecting Docker/Linux.
        homebrew_bin = Path("/opt/homebrew/bin")
        if homebrew_bin.exists():
            os.environ["PATH"] = f"{homebrew_bin}{os.pathsep}{os.environ.get('PATH', '')}"

        settings = cls(
            base_dir=base_dir,
            frontend_dir=base_dir / "frontend",
            upload_dir=base_dir / "uploads",
            output_dir=base_dir / "outputs",
            work_dir=base_dir / "work",
            ffmpeg=cls._resolve_binary(base_dir, "ffmpeg"),
            ffprobe=cls._resolve_binary(base_dir, "ffprobe"),
            paddle_ocr_enabled=cls._paddle_ocr_enabled(),
            worker_concurrency=cls._positive_int("VIDTRANS_WORKER_CONCURRENCY", 2),
            whisper_concurrency=cls._positive_int("VIDTRANS_WHISPER_CONCURRENCY", 1),
            ocr_concurrency=cls._positive_int("VIDTRANS_OCR_CONCURRENCY", 1),
        )
        settings.ensure_directories()
        return settings

    @staticmethod
    def _resolve_binary(base_dir: Path, name: str) -> str:
        candidates = [
            shutil.which(name),
            str((base_dir.parent / "ffmpeg" / name).resolve()),
            str((base_dir.parent / "ffmpeg").resolve()),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return name

    @staticmethod
    def _paddle_ocr_enabled() -> bool:
        """Avoid a known native Paddle crash in Docker Desktop ARM64 by default.

        Set VIDTRANS_ENABLE_PADDLE_OCR=1 after validating a compatible Paddle
        runtime.  An explicit 0 always disables the optional OCR stage.
        """
        configured = os.environ.get("VIDTRANS_ENABLE_PADDLE_OCR")
        if configured is not None:
            return configured.lower() in {"1", "true", "yes"}
        return not (platform.system() == "Linux" and platform.machine().lower() in {"arm64", "aarch64"})

    @staticmethod
    def _positive_int(name: str, default: int) -> int:
        raw_value = os.environ.get(name, str(default))
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if value < 1:
            raise ValueError(f"{name} must be at least 1")
        return value

    def ensure_directories(self) -> None:
        for path in (self.upload_dir, self.output_dir, self.work_dir):
            path.mkdir(parents=True, exist_ok=True)
