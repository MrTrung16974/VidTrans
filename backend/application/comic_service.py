"""Application boundary of the story -> comic video feature.

A comic project is an ordinary VidTrans job (``job_action = "comic_video"``), so
it inherits the persistent queue, cancellation, progress events, quota and
ownership checks. The editable storyboard and asset caches live in the job's
work directory; re-rendering requeues the same job and reuses every asset whose
inputs did not change.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from domain.comic import ComicSettings
from infrastructure.comic_project import ComicProject
from infrastructure.image_providers import create_image_provider
from infrastructure.llm_providers import create_story_llm
from pipeline.comic_storyboard import build_storyboard
from pipeline.comic_video import ComicRuntime, ComicVideoProducer, remove_stale_assets

logger = logging.getLogger(__name__)

COMIC_JOB_ACTION = "comic_video"
ACTIVE_STATUSES = {"queued", "scheduled", "processing", "cancelling"}

# Share of the job progress bar per stage, for the full render and the image-only preview.
RENDER_STAGES = {
    "comic_storyboard": (0.02, 0.15), "comic_references": (0.15, 0.22), "comic_images": (0.22, 0.6),
    "comic_voice": (0.6, 0.75), "comic_clips": (0.75, 0.95), "comic_export": (0.95, 0.99),
}
PREVIEW_STAGES = {
    "comic_storyboard": (0.02, 0.2), "comic_references": (0.2, 0.3), "comic_images": (0.3, 0.99),
}


def is_comic_job(job: dict[str, Any] | None) -> bool:
    return bool(job) and job.get("job_action") == COMIC_JOB_ACTION


def comic_summary(settings: ComicSettings, storyboard=None, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "title": settings.title, "style": settings.style, "aspect_ratio": settings.aspect_ratio,
        "image_provider": settings.image_provider, "llm_provider": settings.llm_provider,
    }
    if storyboard is not None:
        summary.update(scenes=len(storyboard.scenes), characters=len(storyboard.characters),
                       revision=storyboard.revision, analysis_method=storyboard.analysis_method,
                       warnings=len(storyboard.warnings))
    if manifest:
        summary.update(duration=manifest.get("duration"), rendered_revision=manifest.get("revision"),
                       cache=manifest.get("cache"))
    return summary


@dataclass
class ComicJobRunner:
    get_job: Callable[[str], dict[str, Any] | None]
    update_job: Callable[..., None]
    ensure_active: Callable[[str], None]
    project_root: Callable[[str, str], Path]
    output_dir: Callable[[str, str], Path]
    artifact_name: Callable[[Path], str]
    make_runtime: Callable[[str, Callable[[str, float, str], None]], ComicRuntime]
    font_path: Path | None
    utc_now: Callable[[], str]

    def run(self, job_id: str, request: dict[str, Any]) -> None:
        job = self.get_job(job_id) or {}
        owner = str(job.get("owner_user_id") or "__legacy__")
        settings = ComicSettings.model_validate(request["settings"])
        render = bool(request.get("render", settings.auto_render))
        stages = RENDER_STAGES if render else PREVIEW_STAGES
        project = ComicProject(self.project_root(owner, job_id))
        music_value = request.get("music_path")
        music = Path(str(music_value)) if music_value else None
        if music is not None and not music.is_file():
            raise FileNotFoundError("File nhạc nền đã tải lên không còn tồn tại")

        def report(step: str, fraction: float, detail: str) -> None:
            low, high = stages.get(step, (0.0, 0.99))
            self.update_job(job_id, status="processing", step=step, step_detail=detail,
                            progress=round(low + (high - low) * max(0.0, min(1.0, fraction)), 4))

        self.update_job(job_id, status="processing", step="comic_storyboard", progress=0.02,
                        step_detail="Phân tích truyện", started_at=self.utc_now(), error=None)
        if not project.has_storyboard():
            llm = create_story_llm(settings.llm_provider)
            storyboard = build_storyboard(
                project.read_story(), settings, llm,
                progress=lambda done, total: report("comic_storyboard", done / total,
                                                    f"Chia cảnh đoạn {done}/{total}"),
                check_active=lambda: self.ensure_active(job_id),
            )
            project.save_storyboard(storyboard, bump=False)
        storyboard = project.load_storyboard()
        self.update_job(job_id, comic=comic_summary(settings, storyboard))

        producer = ComicVideoProducer(
            project, settings, create_image_provider(settings.image_provider, font_path=self.font_path),
            self.make_runtime(job_id, report),
        )
        if not render:
            manifest = producer.preview_images(storyboard)
            self.update_job(job_id, status="completed", step="storyboard_ready", progress=1.0,
                            step_detail="Storyboard và ảnh đã sẵn sàng để duyệt",
                            comic=comic_summary(settings, storyboard, manifest), finished_at=self.utc_now())
            return

        output_dir = self.output_dir(owner, job_id)
        video = output_dir / f"comic_{job_id}.mp4"
        srt = output_dir / f"comic_{job_id}.srt"
        manifest = producer.produce(storyboard, video, srt, music)
        remove_stale_assets(project, manifest)
        self.update_job(
            job_id, status="completed", step="completed", progress=1.0,
            step_detail=f"Đã xuất {len(storyboard.scenes)} cảnh · dùng lại {manifest['cache']['hits']} tài nguyên",
            output_video=self.artifact_name(video), subtitle_file=self.artifact_name(srt),
            comic=comic_summary(settings, storyboard, manifest), finished_at=self.utc_now(),
        )
