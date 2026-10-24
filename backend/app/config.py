from __future__ import annotations

import importlib.util
import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3
# A long video peaked at ~3.5 GB RSS in Whisper (~1.6 GB since features are chunked);
# the budget also covers rendering. The reserve is headroom on top of what the OS and
# co-located services (TTS server, browser containers) already use at startup.
JOB_MEMORY_BYTES = 4 * GIB
RESERVED_MEMORY_BYTES = 2 * GIB
CPUS_PER_JOB = 2
MAX_AUTO_WORKERS = 8


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
    ocr_backend: str
    ocr_model: str
    worker_concurrency: int
    whisper_concurrency: int
    ocr_concurrency: int
    max_job_restarts: int

    @classmethod
    def load(cls, base_dir: Path) -> "AppSettings":
        base_dir = Path(base_dir).resolve()
        # Keep native macOS development working without affecting Docker/Linux.
        homebrew_bin = Path("/opt/homebrew/bin")
        if homebrew_bin.exists():
            os.environ["PATH"] = f"{homebrew_bin}{os.pathsep}{os.environ.get('PATH', '')}"

        ocr_backend = cls._ocr_backend()
        worker_concurrency = cls._worker_concurrency()
        settings = cls(
            base_dir=base_dir,
            # The web client is its own app (repo-root web/, served by the vidtrans-web
            # container). The API serves it only for single-process local development.
            frontend_dir=Path(os.environ.get("VIDTRANS_WEB_DIR") or base_dir.parent / "web").resolve(),
            upload_dir=base_dir / "uploads",
            output_dir=base_dir / "outputs",
            work_dir=base_dir / "work",
            ffmpeg=cls._resolve_binary(base_dir, "ffmpeg"),
            ffprobe=cls._resolve_binary(base_dir, "ffprobe"),
            ocr_backend=ocr_backend,
            ocr_model=cls._ocr_model(),
            worker_concurrency=worker_concurrency,
            # Each job's memory budget already covers its own ASR/OCR pass, so by
            # default every running job may transcribe at once; upgrading the server
            # raises worker_concurrency and these follow.
            whisper_concurrency=cls._stage_concurrency("VIDTRANS_WHISPER_CONCURRENCY", worker_concurrency),
            ocr_concurrency=cls._stage_concurrency("VIDTRANS_OCR_CONCURRENCY", worker_concurrency),
            # A job that keeps crashing the worker (usually an OOM kill) must not
            # be restarted forever on every container restart.
            max_job_restarts=cls._positive_int("VIDTRANS_MAX_JOB_RESTARTS", 3),
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
    def _ocr_backend() -> str:
        configured = os.environ.get("VIDTRANS_OCR_BACKEND")
        if configured is not None:
            backend = configured.strip().lower()
            if backend not in {"auto", "paddle", "tesseract", "off"}:
                raise ValueError("VIDTRANS_OCR_BACKEND must be auto, paddle, tesseract, or off")
            if backend != "auto":
                return backend

        # PaddleOCR reads stylised burned-in captions far more accurately
        # than Tesseract, and it now runs on Linux ARM too. Tesseract is only
        # the fallback where the Paddle wheels are not installed.
        if platform.system() == "Linux" and platform.machine().lower() in {"arm64", "aarch64"}:
            return "paddle" if importlib.util.find_spec("paddleocr") else "tesseract"
        return "paddle"

    @staticmethod
    def _ocr_model() -> str:
        model = os.environ.get("VIDTRANS_OCR_MODEL", "medium").strip().lower()
        if model not in {"medium", "mobile"}:
            raise ValueError("VIDTRANS_OCR_MODEL must be medium or mobile")
        return model

    @classmethod
    def _worker_concurrency(cls) -> int:
        raw_value = os.environ.get("VIDTRANS_WORKER_CONCURRENCY", "auto").strip().lower()
        if raw_value not in {"", "auto", "0"}:
            return cls._positive_int("VIDTRANS_WORKER_CONCURRENCY", 1)
        return auto_worker_concurrency(available_memory_bytes(), available_cpu_count())

    @classmethod
    def _stage_concurrency(cls, name: str, worker_concurrency: int) -> int:
        if os.environ.get(name, "auto").strip().lower() in {"", "auto", "0"}:
            return worker_concurrency
        return cls._positive_int(name, 1)

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


def auto_worker_concurrency(memory_bytes: int | None, cpu_count: int) -> int:
    """Run as many jobs as both RAM and CPU can carry; one always runs."""
    by_cpu = cpu_count // CPUS_PER_JOB
    by_memory = by_cpu if memory_bytes is None else (memory_bytes - RESERVED_MEMORY_BYTES) // JOB_MEMORY_BYTES
    return max(1, min(by_cpu, by_memory, MAX_AUTO_WORKERS))


def available_cpu_count() -> int:
    try:
        cpus = len(os.sched_getaffinity(0))
    except AttributeError:  # macOS has no CPU affinity API
        cpus = os.cpu_count() or 1
    quota = _cgroup_cpu_quota()
    return max(1, min(cpus, quota) if quota else cpus)


def available_memory_bytes() -> int | None:
    """RAM this process may use: the container's cgroup limit when one is set, otherwise
    physical RAM minus what other processes on the host already hold.

    The VPS runs the TTS server and two Chromium containers next to VidTrans (~3-4 GB);
    counting them as free sized the worker pool for RAM that was not there.
    """
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        total = None
    for path in (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            limit = int(path.read_text().strip())
        except (OSError, ValueError):  # missing file, or "max" for no limit
            continue
        if total is None or limit < total:
            return limit
    others = _memory_used_by_other_processes()
    if total is not None and others is not None:
        total = max(0, total - others)
    return total


def _memory_used_by_other_processes() -> int | None:
    """Host memory in use outside this process, from /proc/meminfo (host-wide in Docker)."""
    try:
        fields = {
            line.split(":")[0]: int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.split(":")[0] in {"MemTotal", "MemAvailable"}
        }
        used = fields["MemTotal"] - fields["MemAvailable"]
    except (OSError, KeyError, ValueError, IndexError):
        return None
    own = 0
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                own = int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return max(0, used - own)


def _cgroup_cpu_quota() -> int | None:
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        return max(1, int(quota) // int(period))
    except (OSError, ValueError):  # missing file, or "max" for no quota
        return None
