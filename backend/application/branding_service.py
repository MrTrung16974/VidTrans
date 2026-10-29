"""Application boundary of "stamp my logo on this video".

A logo job is an ordinary VidTrans job (``job_action = "logo_branding"``), so it
shares the persistent queue, cancellation, progress events, quota, retention and
ownership checks with video translation. When queued, the account's logo and
settings are copied into the job (``resume_request``): editing them later never
changes a job that is already waiting or running.

    probe -> detect logo (optional) -> plan placements -> FFmpeg render -> artifact
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from domain.branding import BrandingConfig
from infrastructure.media_probe import probe_media_file
from pipeline.logo_detection import Box, Detection, Segment, detect_logo
from pipeline.logo_overlay import build_ffmpeg_command, plan_placements

logger = logging.getLogger(__name__)

BRANDING_JOB_ACTION = "logo_branding"
STAGES = {"probe": (0.0, 0.05), "detect": (0.05, 0.35), "render": (0.35, 0.99)}


def is_branding_job(job: dict[str, Any] | None) -> bool:
    return bool(job) and job.get("job_action") == BRANDING_JOB_ACTION


def segments_from_dicts(items: list[dict[str, Any]]) -> list[Segment]:
    return [
        Segment(float(item["start"]), float(item["end"]), Box(**item["box"]) if item.get("box") else None,
                float(item.get("confidence") or 0.0))
        for item in items
    ]


@dataclass
class BrandingJobRunner:
    update_job: Callable[..., None]
    ensure_active: Callable[[str], None]
    output_dir: Callable[[str, str], Path]
    artifact_name: Callable[[Path], str]
    get_job: Callable[[str], dict[str, Any] | None]
    ffmpeg: Callable[[], str]
    ffprobe: Callable[[], str]
    utc_now: Callable[[], str]

    def _progress(self, job_id: str, stage: str, fraction: float, detail: str) -> None:
        low, high = STAGES[stage]
        self.update_job(job_id, status="processing", step=f"branding_{stage}",
                        progress=round(low + (high - low) * max(0.0, min(1.0, fraction)), 3), step_detail=detail)

    def run(self, job_id: str, request: dict[str, Any]) -> None:
        job = self.get_job(job_id) or {}
        owner = str(job.get("owner_user_id") or "__legacy__")
        config = BrandingConfig.model_validate(request["config"])
        override = str(request.get("override") or "auto")
        video_path = Path(str(request["video_path"]))
        logo_path = Path(str(request["logo_path"]))
        if not video_path.is_file():
            raise FileNotFoundError("Video nguồn không còn trên máy chủ")
        if not logo_path.is_file():
            raise FileNotFoundError("Logo của job không còn trên máy chủ")

        self._progress(job_id, "probe", 0.0, "Đang đọc thông tin video")
        probe = probe_media_file(video_path, ffprobe=self.ffprobe())
        self.ensure_active(job_id)

        detection: Detection | None = None
        if config.auto_detect and override == "auto":
            self._progress(job_id, "detect", 0.0, "Đang nhận diện logo có sẵn trong video")
            detection = detect_logo(
                self.ffmpeg(), video_path, width=probe.width, height=probe.height, duration=probe.duration,
                strategy=config.multi_logo_strategy, default_position=config.default_position,
                min_confidence=config.min_confidence,
            )
            self.ensure_active(job_id)
        placements = plan_placements(
            detection.segments if detection else None,
            duration=probe.duration, video_w=probe.width, video_h=probe.height,
            logo_w=int(request["logo_width"]), logo_h=int(request["logo_height"]),
            size_percent=config.logo_size, margin_percent=config.margin,
            default_position=config.default_position, cover_original=config.cover_original, override=override,
        )
        needs_review = bool(detection and detection.status == "uncertain" and config.low_confidence_action == "review")
        summary = {
            "override": override,
            "video": {"width": probe.width, "height": probe.height, "duration": probe.duration,
                      "has_audio": probe.has_audio},
            "detection": detection.to_dict() if detection else None,
            "detected_logo": bool(detection and detection.status == "found"),
            "placements": [placement.to_dict() for placement in placements],
            "needs_review": needs_review,
        }
        self.update_job(job_id, branding=summary)

        output = self.output_dir(owner, job_id) / f"{video_path.stem}.branded.mp4"
        detail = f"Đang chèn logo ({len(placements)} vị trí)" if len(placements) > 1 else "Đang chèn logo"
        self._progress(job_id, "render", 0.0, detail)
        try:
            self._render(job_id, video_path, logo_path, output, placements, config.opacity, probe.duration,
                         copy_audio=True)
        except subprocess.CalledProcessError:
            # Some source audio codecs (Vorbis, PCM in MKV...) cannot go into MP4 as-is.
            logger.info("Logo job %s: audio copy failed, re-encoding audio to AAC", job_id)
            self._render(job_id, video_path, logo_path, output, placements, config.opacity, probe.duration,
                         copy_audio=False)
        self.update_job(
            job_id, status="completed", step="completed", progress=1.0,
            step_detail="Cần xác nhận vị trí logo" if needs_review else "Đã chèn logo",
            output_video=self.artifact_name(output), branding=summary, finished_at=self.utc_now(),
        )

    def _render(self, job_id: str, video_path: Path, logo_path: Path, output: Path, placements, opacity: float,
                duration: float, *, copy_audio: bool) -> None:
        temporary = output.with_name(f".{output.name}.part.mp4")
        command = build_ffmpeg_command(self.ffmpeg(), video_path, logo_path, temporary, placements,
                                       opacity=opacity, copy_audio=copy_audio)
        command[1:1] = ["-progress", "pipe:1", "-nostats"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and value.isdigit() and duration > 0:
                    self._progress(job_id, "render", int(value) / 1_000_000 / duration, "Đang chèn logo")
                    self.ensure_active(job_id)
            stderr = process.stderr.read()
            if process.wait() != 0:
                temporary.unlink(missing_ok=True)
                raise subprocess.CalledProcessError(process.returncode, command, stderr=stderr)
        except BaseException:
            process.kill()
            process.wait()
            temporary.unlink(missing_ok=True)
            raise
        temporary.replace(output)
