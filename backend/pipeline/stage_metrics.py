from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterator


logger = logging.getLogger(__name__)

STAGE_NAMES = frozenset(
    {
        "preflight",
        "ocr",
        "asr",
        "caption_alignment",
        "audio_scene",
        "vocal_separation",
        "translation",
        "summary",
        "voice_routing",
        "tts",
        "audio_mix",
        "subtitle_artifacts",
        "render",
    }
)


@dataclass(frozen=True)
class StageMeasurement:
    stage: str
    status: str
    duration_seconds: float
    recorded_at: str
    real_time_factor: float | None = None

    def as_dict(self) -> dict[str, str | float]:
        payload: dict[str, str | float] = {
            "status": self.status,
            "duration_seconds": self.duration_seconds,
            "recorded_at": self.recorded_at,
        }
        if self.real_time_factor is not None:
            payload["real_time_factor"] = self.real_time_factor
        return payload


MeasurementCallback = Callable[[StageMeasurement], None]


def _status_for_error(error: BaseException) -> str:
    return "cancelled" if type(error).__name__ in {"JobCancelled", "CancelledError"} else "failed"


@contextmanager
def measure_stage(
    stage: str,
    *,
    video_duration: float | None = None,
    on_complete: MeasurementCallback | None = None,
    clock: Callable[[], float] = time.perf_counter,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Iterator[None]:
    """Measure a pipeline stage without ever masking the stage's own error."""

    if stage not in STAGE_NAMES:
        raise ValueError(f"unknown pipeline stage: {stage}")
    started = clock()
    status = "completed"
    try:
        yield
    except BaseException as exc:
        status = _status_for_error(exc)
        raise
    finally:
        elapsed = round(max(0.0, clock() - started), 6)
        rtf = None
        if video_duration is not None and video_duration > 0:
            rtf = round(elapsed / video_duration, 6)
        measurement = StageMeasurement(
            stage=stage,
            status=status,
            duration_seconds=elapsed,
            real_time_factor=rtf,
            recorded_at=now().astimezone(timezone.utc).isoformat(),
        )
        if on_complete is not None:
            try:
                on_complete(measurement)
            except Exception:
                logger.exception("Unable to persist pipeline stage metric for %s", stage)
