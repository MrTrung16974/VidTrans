"""Application boundary of the story -> comic video feature.

A comic project is an ordinary VidTrans job (``job_action = "comic_video"``), so
it inherits the persistent queue, cancellation, progress events, quota and
ownership checks. The editable storyboard and asset caches live in the job's
work directory; re-rendering requeues the same job and reuses every asset whose
inputs did not change.

A project may be one episode of a series (``resume_request["series"]``): it then
shares the series' character bible and pinned references with its sibling
episodes (see ``infrastructure.comic_series``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from domain.comic import ComicSettings
from infrastructure.comic_project import ComicProject
from infrastructure.comic_series import ComicSeriesStore
from infrastructure.image_providers import create_image_provider
from infrastructure.llm_providers import create_story_llm
from infrastructure.video_providers import Camera25DProvider, create_video_provider
from infrastructure.visual_prompt_translator import (
    ChainedVisualPromptTranslator,
    LLMVisualPromptTranslator,
    LocalVisualPromptTranslator,
)
from pipeline.animation_video import AnimatedVideoProducer
from pipeline.comic_storyboard import build_storyboard, fill_english, needs_english
from pipeline.comic_video import ComicRuntime, ComicVideoProducer, remove_stale_assets

logger = logging.getLogger(__name__)

COMIC_JOB_ACTION = "comic_video"
ACTIVE_STATUSES = {"queued", "scheduled", "processing", "cancelling"}

# Share of the job progress bar per stage, for the full render and the image-only preview.
# A full comic render builds each scene's image, voice and clip together, so those share one range.
_SCENE_RANGE = (0.22, 0.95)
RENDER_STAGES = {
    "comic_storyboard": (0.02, 0.15), "comic_references": (0.15, 0.22), "comic_images": _SCENE_RANGE,
    "comic_voice": _SCENE_RANGE, "comic_clips": _SCENE_RANGE, "comic_export": (0.95, 0.99),
}
PREVIEW_STAGES = {
    "comic_storyboard": (0.02, 0.2), "comic_references": (0.2, 0.3), "comic_images": (0.3, 0.99),
    "comic_keyframes": (0.3, 0.99),
}
# Animated shots: keyframes and voices for all shots, then AI motion shot by shot, then the clips.
ANIMATED_STAGES = {
    "comic_storyboard": (0.02, 0.15), "comic_references": (0.15, 0.2), "comic_keyframes": (0.2, 0.35),
    "comic_voice": (0.2, 0.35), "comic_animation": (0.35, 0.88), "comic_clips": (0.88, 0.95),
    "comic_export": (0.95, 0.99),
}


def visual_translator(llm) -> ChainedVisualPromptTranslator:
    """Vietnamese -> English for image prompts: the offline Marian model (optional, needs
    transformers) first, then the storyboard LLM when there is one."""
    return ChainedVisualPromptTranslator(
        LocalVisualPromptTranslator(), LLMVisualPromptTranslator(llm) if llm is not None else None,
    )


def is_comic_job(job: dict[str, Any] | None) -> bool:
    return bool(job) and job.get("job_action") == COMIC_JOB_ACTION


def comic_summary(settings: ComicSettings, storyboard=None, manifest: dict[str, Any] | None = None,
                  series: dict[str, Any] | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "title": settings.title,
        "youtube_title": ((getattr(storyboard, "youtube_title", "") if storyboard is not None else "")
                          or settings.youtube_title or settings.title)[:100],
        "style": settings.style, "aspect_ratio": settings.aspect_ratio,
        "image_provider": settings.image_provider, "llm_provider": settings.llm_provider,
    }
    if storyboard is not None:
        summary.update(scenes=len(storyboard.scenes), characters=len(storyboard.characters),
                       revision=storyboard.revision, analysis_method=storyboard.analysis_method,
                       warnings=len(storyboard.warnings))
    if manifest:
        summary.update(duration=manifest.get("duration"), rendered_revision=manifest.get("revision"),
                       cache=manifest.get("cache"), metrics=manifest.get("metrics"),
                       image_fallback=manifest.get("image_fallback"))
    summary["video_mode"] = settings.video_mode
    if series:
        summary.update(series_id=series.get("series_id"), series_title=series.get("series_title"),
                       episode_number=series.get("episode_number"))
    if settings.video_mode == "animated":
        summary["animation_provider"] = settings.animation_provider
        if storyboard is not None:
            summary["shots"] = sum(len(scene.shots) for scene in storyboard.scenes)
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
    series_store: ComicSeriesStore | None = None

    def run(self, job_id: str, request: dict[str, Any]) -> None:
        job = self.get_job(job_id) or {}
        owner = str(job.get("owner_user_id") or "__legacy__")
        settings = ComicSettings.model_validate(request["settings"])
        render = bool(request.get("render", settings.auto_render))
        preview_shot = request.get("preview_shot")
        series = request.get("series") if self.series_store is not None else None
        series_id = str(series["series_id"]) if series else ""

        def summary(storyboard=None, manifest=None) -> dict[str, Any]:
            return comic_summary(settings, storyboard, manifest, series)

        def absorb(storyboard, manifest=None) -> None:
            if series_id:
                self.series_store.absorb_episode(owner, series_id, project, storyboard, manifest)
        animated = settings.video_mode == "animated"
        stages = PREVIEW_STAGES if not render else ANIMATED_STAGES if animated else RENDER_STAGES
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
            prompt_translator = visual_translator(llm)
            storyboard = build_storyboard(
                project.read_story(), settings, llm,
                progress=lambda done, total: report("comic_storyboard", done / total,
                                                    f"Chia cảnh đoạn {done}/{total}"),
                check_active=lambda: self.ensure_active(job_id),
                known_characters=self.series_store.known_characters(owner, series_id) if series_id else [],
                known_locations=self.series_store.known_locations(owner, series_id) if series_id else [],
                known_world=self.series_store.known_world(owner, series_id) if series_id else "",
                known_world_vi=self.series_store.known_world_vi(owner, series_id) if series_id else "",
                # Its local model is Vietnamese -> English; other narration languages rely on the LLM.
                prompt_translator=prompt_translator if settings.language == "vi" else None,
            )
            project.save_storyboard(storyboard, bump=False)
        storyboard = project.load_storyboard()
        # Looks edited in Vietnamese since the last run: translate them before anything is drawn.
        looks = [*storyboard.characters, *storyboard.locations, *storyboard.scenes]  # Scenes: fix notes.
        if needs_english(looks, storyboard):
            try:
                llm = create_story_llm(settings.llm_provider)
            except Exception:  # Ollama down: the offline model or the Vietnamese text still works.
                llm = None
            fill_english(looks, visual_translator(llm), world_holder=storyboard)
            storyboard = project.save_storyboard(storyboard, bump=False)
        if series_id:
            storyboard = self.series_store.sync_into_episode(owner, series_id, project, storyboard)
            absorb(storyboard)
        self.update_job(job_id, filename=storyboard.youtube_title or settings.title,
                        comic=summary(storyboard))

        runtime = self.make_runtime(job_id, report)
        images = create_image_provider(settings.image_provider, font_path=self.font_path)
        if animated:
            if storyboard.mode != "animated":
                raise ValueError("Storyboard này được tạo cho truyện tranh; hãy bấm Phân tích lại để chia shot")
            producer = AnimatedVideoProducer(
                project, settings, images,
                create_video_provider(settings.animation_provider, ffmpeg=runtime.ffmpeg, run_ffmpeg=runtime.run_ffmpeg),
                Camera25DProvider(runtime.ffmpeg, runtime.run_ffmpeg), runtime,
            )
        else:
            producer = ComicVideoProducer(project, settings, images, runtime)
        if animated and preview_shot:
            manifest = producer.preview_shot(storyboard, str(preview_shot))
            absorb(storyboard, manifest)
            self.update_job(job_id, status="completed", step="shot_preview_ready", progress=1.0,
                            step_detail=f"Đã dựng xem trước shot {preview_shot}",
                            comic=summary(storyboard, manifest), finished_at=self.utc_now())
            return
        if not render:
            scene_limit = int(request.get("preview_scenes") or 0) or None
            scene_indexes = [int(index) for index in request.get("preview_scene_indexes") or []] or None
            manifest = producer.preview_images(storyboard, scene_limit, scene_indexes)
            absorb(storyboard, manifest)
            self.update_job(job_id, status="completed", step="storyboard_ready", progress=1.0,
                            step_detail=(f"Đã vẽ lại cảnh {', '.join(map(str, scene_indexes))}" if scene_indexes
                                         else f"Đã vẽ thử {min(scene_limit, len(storyboard.scenes))} cảnh đầu — "
                                         "duyệt rồi bấm Render video" if scene_limit
                                         else "Storyboard và ảnh đã sẵn sàng để duyệt"),
                            comic=summary(storyboard, manifest), finished_at=self.utc_now())
            return

        output_dir = self.output_dir(owner, job_id)
        video = output_dir / f"comic_{job_id}.mp4"
        srt = output_dir / f"comic_{job_id}.srt"
        manifest = producer.produce(storyboard, video, srt, music)
        remove_stale_assets(project, manifest)
        absorb(storyboard, manifest)
        self.update_job(
            job_id, status="completed", step="completed", progress=1.0,
            step_detail=_completion_detail(storyboard, manifest),
            output_video=self.artifact_name(video), subtitle_file=self.artifact_name(srt),
            comic=summary(storyboard, manifest), finished_at=self.utc_now(),
        )


def _completion_detail(storyboard, manifest: dict[str, Any]) -> str:
    detail = _render_detail(storyboard, manifest)
    fallback = manifest.get("image_fallback")
    if fallback:
        detail += (f" · {fallback['count']} ảnh dùng minh họa tạm vì AI vẽ ảnh lỗi ({fallback['reason']});"
                   " bấm Render lại khi còn hạn mức để thay bằng ảnh AI")
    return detail


def _render_detail(storyboard, manifest: dict[str, Any]) -> str:
    reused = manifest["cache"]["hits"]
    if manifest.get("mode") != "animated":
        return f"Đã xuất {len(storyboard.scenes)} cảnh · dùng lại {reused} tài nguyên"
    metrics = manifest.get("metrics") or {}
    detail = f"Đã xuất {len(manifest['shots'])} shot · dùng lại {reused} tài nguyên · AI {metrics.get('ai_seconds', 0):g}s"
    if metrics.get("fallback_shots"):
        detail += f" · {metrics['fallback_shots']} shot dùng camera 2.5D dự phòng"
    return detail
