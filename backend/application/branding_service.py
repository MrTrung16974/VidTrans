"""Application boundary of "stamp my logo on this video".

A logo job is an ordinary VidTrans job (``job_action = "logo_branding"``), so it
shares the persistent queue, cancellation, progress events, quota, retention and
ownership checks with video translation. When queued, the account's logo and
settings are copied into the job (``resume_request``): editing them later never
changes a job that is already waiting or running.

    probe -> find the old logo (template tracking when the user gave a sample of it,
    else persistent-edge detection) -> plan placements -> render -> artifact

Rendering is one FFmpeg pass, or, when the old logo is inpainted, frames stream
through ``pipeline.logo_inpaint`` between a decoder and the encoder.
"""
from __future__ import annotations

import io
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from domain.branding import BrandingConfig
from infrastructure.media_probe import probe_media_file
from pipeline import logo_inpaint, logo_tracking
from pipeline.logo_detection import Detection, detect_logo, hold_one_position
from pipeline.logo_overlay import Placement, build_ffmpeg_command, plan_placements

logger = logging.getLogger(__name__)

BRANDING_JOB_ACTION = "logo_branding"
STAGES = {"probe": (0.0, 0.05), "detect": (0.05, 0.35), "render": (0.35, 0.99)}


def is_branding_job(job: dict[str, Any] | None) -> bool:
    return bool(job) and job.get("job_action") == BRANDING_JOB_ACTION


def decoded_size(ffmpeg: str, video_path: Path) -> tuple[int, int] | None:
    """Frame size as FFmpeg decodes it, i.e. after rotation metadata (phone videos) is applied."""
    from PIL import Image

    result = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(video_path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        capture_output=True, timeout=60,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    with Image.open(io.BytesIO(result.stdout)) as image:
        return image.size


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
        video_path = Path(str(request["video_path"]))
        if not video_path.is_file():
            raise FileNotFoundError("Video nguồn không còn trên máy chủ")
        template_path = Path(str(request["template_path"])) if request.get("template_path") else None
        output = self.output_dir(owner, job_id) / f"{video_path.stem}.branded.mp4"
        summary = self.stamp(
            job_id, video_path, output, logo_path=Path(str(request["logo_path"])),
            logo_width=int(request["logo_width"]), logo_height=int(request["logo_height"]), config=config,
            template_path=template_path, override=str(request.get("override") or "auto"),
            progress=lambda stage, fraction, detail: self._progress(job_id, stage, fraction, detail),
        )
        self.update_job(
            job_id, status="completed", step="completed", progress=1.0,
            step_detail="Cần xác nhận vị trí logo" if summary["needs_review"] else "Đã chèn logo",
            output_video=self.artifact_name(output), branding=summary, finished_at=self.utc_now(),
        )

    def detect_video(self, video_path: Path, config: BrandingConfig, *, template_path: Path | None = None,
                     progress: Callable[[float, str], None] = lambda fraction, detail: None) -> Detection:
        """Where the old logo is: one position for the whole video (no logo: no position).

        Writes nothing to the job, so translation jobs run it on the source video in
        another thread while the slow stages run (the render keeps the frame geometry).
        """
        probe = probe_media_file(video_path, ffprobe=self.ffprobe())
        width, height = decoded_size(self.ffmpeg(), video_path) or (probe.width, probe.height)
        if template_path is not None and template_path.is_file() and logo_tracking.available():
            # The user named the logo to replace: follow it, and never replace another one instead.
            progress(0.0, "Đang dò logo theo mẫu")
            detection = logo_tracking.track_template(
                self.ffmpeg(), video_path, template_path, width=width, height=height,
                duration=probe.duration, progress=lambda fraction: progress(fraction, "Đang dò logo theo mẫu"),
            )
        else:
            progress(0.0, "Đang nhận diện logo có sẵn trong video")
            detection = detect_logo(
                self.ffmpeg(), video_path, width=width, height=height, duration=probe.duration,
                strategy=config.multi_logo_strategy, default_position=config.default_position,
                min_confidence=config.min_confidence,
            )
        # The new logo stays in one place for the whole video.
        return hold_one_position(detection, probe.duration)

    def stamp(self, job_id: str, video_path: Path, output: Path, *, logo_path: Path, logo_width: int,
              logo_height: int, config: BrandingConfig, template_path: Path | None = None,
              override: str = "auto", progress: Callable[[str, float, str], None],
              prepared: Callable[[], Detection | None] | None = None) -> dict[str, Any]:
        """Stamp the logo on ``video_path`` into ``output`` and return the placement summary.

        Shared by logo jobs and by translation/comic jobs that asked for the account logo;
        ``progress(stage, fraction, detail)`` lets each caller map stages onto its own bar.
        ``prepared`` returns a detection started earlier (``detect_video``); when it returns
        None or fails, the video is analysed here instead.
        """
        if not logo_path.is_file():
            raise FileNotFoundError("Logo của job không còn trên máy chủ")
        progress("probe", 0.0, "Đang đọc thông tin video")
        probe = probe_media_file(video_path, ffprobe=self.ffprobe())
        width, height = decoded_size(self.ffmpeg(), video_path) or (probe.width, probe.height)
        fps = probe.fps if 1.0 <= probe.fps <= 120.0 else 30.0
        self.ensure_active(job_id)

        detection: Detection | None = None
        if config.auto_detect and override == "auto":
            if prepared is not None:
                try:
                    detection = prepared()
                except Exception:
                    logger.exception("Early logo detection failed for job %s; detecting again", job_id)
            if detection is None:
                def detect_progress(fraction: float, detail: str) -> None:
                    progress("detect", fraction, detail)
                    self.ensure_active(job_id)

                detection = self.detect_video(video_path, config, template_path=template_path,
                                              progress=detect_progress)
            self.ensure_active(job_id)
        placements, summary = self._plan(probe, width, height, fps, logo_width=logo_width, logo_height=logo_height,
                                         config=config, detection=detection, override=override)
        inpaint = summary["cover_method"] == "inpaint"
        self.update_job(job_id, branding=summary)

        detail = f"Đang chèn logo ({len(placements)} vị trí)" if len(placements) > 1 else "Đang chèn logo"
        progress("render", 0.0, detail)
        repaired = inpaint and any(placement.cover_box for placement in placements)
        for copy_audio in (True, False):
            try:
                if repaired:
                    self._render_inpainted(job_id, video_path, logo_path, output, placements,
                                           detection.stencils if detection else {}, width, height, fps,
                                           probe.duration, config.opacity, copy_audio=copy_audio,
                                           progress=progress)
                else:
                    self._render(job_id, video_path, logo_path, output, placements, config.opacity,
                                 probe.duration, copy_audio=copy_audio, progress=progress)
                break
            except subprocess.CalledProcessError:
                if not copy_audio:
                    raise
                # Some source audio codecs (Vorbis, PCM in MKV...) cannot go into MP4 as-is.
                logger.info("Logo job %s: audio copy failed, re-encoding audio to AAC", job_id)
        return summary

    @staticmethod
    def _plan(probe, width: int, height: int, fps: float, *, logo_width: int, logo_height: int,
              config: BrandingConfig, detection: Detection | None, override: str,
              ) -> tuple[list[Placement], dict[str, Any]]:
        """Where the logo goes over time, and the summary stored in the job's ``branding`` field."""
        inpaint = config.cover_original and config.cover_method == "inpaint" and logo_inpaint.available()
        placements = plan_placements(
            detection.segments if detection else None,
            duration=probe.duration, video_w=width, video_h=height,
            logo_w=logo_width, logo_h=logo_height,
            size_percent=config.logo_size, margin_percent=config.margin,
            default_position=config.default_position, cover_original=config.cover_original,
            cover_method="inpaint" if inpaint else "blur", override=override,
        )
        needs_review = bool(detection and detection.status == "uncertain" and config.low_confidence_action == "review")
        summary = {
            "override": override,
            "video": {"width": width, "height": height, "duration": probe.duration, "fps": fps,
                      "has_audio": probe.has_audio},
            "detection": detection.to_dict() if detection else None,
            "detected_logo": bool(detection and detection.status == "found"),
            "cover_method": ("inpaint" if inpaint else "blur") if config.cover_original else None,
            "placements": [placement.to_dict() for placement in placements],
            "needs_review": needs_review,
        }
        return placements, summary

    def plan_for_render(self, video_path: Path, *, logo_width: int, logo_height: int, config: BrandingConfig,
                        template_path: Path | None = None,
                        prepared: Callable[[], Detection | None] | None = None,
                        ) -> tuple[list[Placement], dict[str, Any]] | None:
        """Placements for a render of ``video_path`` that keeps its frame geometry (subtitles),
        so that render draws the logo too and the video is encoded once instead of twice.

        None when the old logo must be inpainted: that needs the frame-by-frame ``stamp``.
        """
        if config.cover_original and config.cover_method == "inpaint" and logo_inpaint.available():
            return None
        probe = probe_media_file(video_path, ffprobe=self.ffprobe())
        width, height = decoded_size(self.ffmpeg(), video_path) or (probe.width, probe.height)
        fps = probe.fps if 1.0 <= probe.fps <= 120.0 else 30.0
        detection: Detection | None = None
        if config.auto_detect:
            detection = prepared() if prepared is not None else None
            if detection is None:
                detection = self.detect_video(video_path, config, template_path=template_path)
        return self._plan(probe, width, height, fps, logo_width=logo_width, logo_height=logo_height,
                          config=config, detection=detection, override="auto")

    def _render_inpainted(self, job_id: str, video_path: Path, logo_path: Path, output: Path, placements,
                          stencils: dict, width: int, height: int, fps: float, duration: float, opacity: float,
                          *, copy_audio: bool, progress: Callable[[str, float, str], None]) -> None:
        temporary = output.with_name(f".{output.name}.part.mp4")

        def render_progress(fraction: float) -> None:
            progress("render", fraction, "Đang khôi phục nền và chèn logo")
            self.ensure_active(job_id)

        try:
            logo_inpaint.render_inpainted(
                self.ffmpeg(), video_path, logo_path, temporary, placements, stencils, width=width,
                height=height, fps=fps, duration=duration, opacity=opacity, copy_audio=copy_audio,
                on_progress=render_progress,
            )
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        temporary.replace(output)

    def _render(self, job_id: str, video_path: Path, logo_path: Path, output: Path, placements, opacity: float,
                duration: float, *, copy_audio: bool, progress: Callable[[str, float, str], None]) -> None:
        temporary = output.with_name(f".{output.name}.part.mp4")
        command = build_ffmpeg_command(self.ffmpeg(), video_path, logo_path, temporary, placements,
                                       opacity=opacity, copy_audio=copy_audio)
        command[1:1] = ["-progress", "pipe:1", "-nostats"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and value.isdigit() and duration > 0:
                    progress("render", int(value) / 1_000_000 / duration, "Đang chèn logo")
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
