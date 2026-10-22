"""Automatic quality gate for generated shots.

It catches what can be measured cheaply: unreadable files, clips far shorter
than requested, clips that barely move (frozen) and clips that are mostly
black. Character distortion and wrong actions still need human review, which
the editor supports by regenerating a single shot.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

MIN_DURATION_RATIO = 0.75
MAX_FROZEN_RATIO = 0.6
MAX_BLACK_RATIO = 0.4

_FREEZE_EVENT = re.compile(r"freeze_(start|end):\s*([0-9.]+)")
_BLACK = re.compile(r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)")


def _frozen_seconds(log: str, duration: float) -> float:
    """freezedetect prints start/end pairs; a freeze that lasts to the end has no end line."""
    total, start = 0.0, None
    for kind, value in _FREEZE_EVENT.findall(log):
        if kind == "start":
            start = float(value)
        elif start is not None:
            total += float(value) - start
            start = None
    if start is not None:
        total += max(0.0, duration - start)
    return total


@dataclass
class QCResult:
    passed: bool
    duration: float = 0.0
    frozen_ratio: float = 0.0
    black_ratio: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "duration": round(self.duration, 3), "frozen_ratio": round(self.frozen_ratio, 3),
                "black_ratio": round(self.black_ratio, 3), "reasons": self.reasons}


def check_clip(path: Path, expected_seconds: float, *, ffmpeg: str, run_ffmpeg: Callable[[list[str]], Any],
               media_duration: Callable[[Path], float], expect_motion: bool = True) -> QCResult:
    if not Path(path).is_file() or Path(path).stat().st_size < 1024:
        return QCResult(False, reasons=["File video rỗng hoặc không tồn tại"])
    try:
        duration = float(media_duration(path) or 0.0)
        analysis = run_ffmpeg([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
                               "freezedetect=n=-60dB:d=0.8,blackdetect=d=0.4:pix_th=0.08", "-f", "null", "-"])
    except (subprocess.CalledProcessError, ValueError, OSError):
        return QCResult(False, reasons=["Không đọc được video"])
    log = getattr(analysis, "stderr", "") or ""
    frozen = _frozen_seconds(log, duration)
    black = sum(float(end) - float(start) for start, end in _BLACK.findall(log))
    result = QCResult(True, duration, frozen / duration if duration else 1.0, black / duration if duration else 1.0)
    if duration < expected_seconds * MIN_DURATION_RATIO:
        result.reasons.append(f"Video chỉ dài {duration:.1f}s / yêu cầu {expected_seconds:.1f}s")
    if expect_motion and result.frozen_ratio > MAX_FROZEN_RATIO:
        result.reasons.append(f"Gần như không chuyển động ({result.frozen_ratio:.0%} khung hình đứng yên)")
    if result.black_ratio > MAX_BLACK_RATIO:
        result.reasons.append(f"Quá nhiều khung hình đen ({result.black_ratio:.0%})")
    result.passed = not result.reasons
    return result
