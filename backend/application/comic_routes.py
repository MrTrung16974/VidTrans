from __future__ import annotations

import hashlib
import io
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from application.comic_service import ACTIVE_STATUSES, COMIC_JOB_ACTION, comic_summary, is_comic_job
from domain.comic import (
    ASPECT_RATIOS,
    CAMERA_MOTIONS,
    COMIC_STYLES,
    MAX_STORY_CHARS,
    SHOT_ANGLES,
    SHOT_MOVEMENTS,
    SHOT_TYPES,
    ComicCharacter,
    ComicScene,
    ComicSettings,
    DialogueLine,
    Shot,
    Storyboard,
)
from domain.languages import LANGUAGES
from domain.plan_entitlements import (
    PlanEntitlementError,
    annotate_providers,
    ensure_comic_settings_allowed,
    has_paid_features,
)
from infrastructure.comic_project import ComicProject
from infrastructure.image_providers import image_provider_statuses
from infrastructure.llm_providers import llm_provider_statuses
from infrastructure.story_text import StoryTextError, normalize_story_text, read_story_upload
from infrastructure.tts_providers import (
    default_tts_provider,
    ensure_provider_configured,
    provider_statuses,
    resolve_tts_voice_selection,
)
from infrastructure.video_providers import animation_provider_statuses

MAX_IMAGE_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_MUSIC_UPLOAD_BYTES = 30 * 1024 * 1024
MUSIC_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg"}


@dataclass
class ComicRouteDependencies:
    current_account: Callable[[Request], dict[str, Any]]
    job_for_request: Callable[[Request, str], dict[str, Any] | None]
    job_response: Callable[[str, dict[str, Any]], dict[str, Any]]
    create_job: Callable[[str, dict[str, Any], str], dict[str, Any]]
    retry_job: Callable[..., dict[str, Any]]
    reserve_quota: Callable[[str, dict[str, Any]], None]
    release_quota: Callable[[str], None]
    quota_account: Callable[[Request, dict[str, Any]], dict[str, Any]]
    notify_scheduler: Callable[[], None]
    project_root: Callable[[str, str], Path]
    upload_dir: Callable[[str, str], Path]
    list_projects: Callable[..., tuple[list[dict[str, Any]], int]] | None = None


class ScenePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narration: str | None = None
    dialogue: list[DialogueLine] | None = None
    characters: list[str] | None = None
    location: str | None = None
    emotion: str | None = None
    visual_prompt: str | None = None
    negative_prompt: str | None = None
    camera_motion: str | None = None


class SceneInsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    after: int = Field(ge=0)
    narration: str = Field(min_length=1, max_length=1500)
    visual_prompt: str = Field(min_length=3, max_length=1500)


class CharacterPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    gender: str | None = None
    age: str | None = None
    appearance: str | None = None
    outfit: str | None = None
    voice: str | None = None


class ShotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration_seconds: float | None = None
    characters: list[str] | None = None
    action: str | None = None
    emotion: str | None = None
    camera: dict[str, str] | None = None
    animation: dict[str, Any] | None = None
    narration: str | None = None
    dialogue: list[DialogueLine] | None = None
    keyframe_prompt: str | None = None
    motion_prompt: str | None = None
    negative_prompt: str | None = None


class ShotInsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    after: int = Field(ge=0)
    keyframe_prompt: str = Field(min_length=3, max_length=1200)
    narration: str = Field(default="", max_length=800)
    motion_prompt: str = Field(default="", max_length=800)


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    images_only: bool = False
    reanalyze: bool = False
    # "scene.shot" (e.g. "2.1"): render only that shot as a preview.
    shot: str | None = Field(default=None, pattern=r"^\d{1,3}\.\d{1,2}$")


def read_upload_png(data: bytes, directory: Path, stem: str) -> str:
    """Validate an uploaded picture and store it as PNG named by its content hash."""
    if len(data) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(400, "Ảnh vượt quá 15 MB")
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.width * image.height > 40_000_000:
                raise HTTPException(400, "Ảnh quá lớn")
            name = f"{stem}-{hashlib.sha256(data).hexdigest()[:16]}.png"
            image.convert("RGB").save(directory / name, format="PNG")
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(400, "File không phải ảnh hợp lệ (PNG, JPG, WEBP)") from exc
    return name


def form_flag(value: str) -> bool:
    # Explicit parsing: an absent checkbox must keep the default, not silently become False.
    return str(value).strip().lower() not in {"0", "false", "off", "no", ""}


def _validation_detail(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'storyboard'}: {e['msg']}" for e in exc.errors())[:600]


def requeue_comic_job(deps: ComicRouteDependencies, request: Request, job_id: str, job: dict[str, Any],
                      *, render: bool, preview_shot: str | None = None,
                      idempotency_key: str | None = None) -> dict[str, Any]:
    """Return a finished comic job to the queue; the worker reuses every unchanged asset."""
    resume_request = dict(job.get("resume_request") or {})
    resume_request["render"] = render
    resume_request["preview_shot"] = preview_shot
    deps.reserve_quota(job_id, deps.quota_account(request, job))
    try:
        deps.retry_job(
            job_id, step="queued", progress=0.0, error=None, error_message=None, step_detail=None,
            cancel_requested=0, worker_id=None, started_at=None, finished_at=None, completed_at=None,
            resume_request=resume_request, comic_idempotency_key=idempotency_key,
        )
    except Exception:
        deps.release_quota(job_id)
        raise
    deps.notify_scheduler()
    return {"job_id": job_id, "status": "queued", "render": render, "preview_shot": preview_shot}


def create_comic_router(deps: ComicRouteDependencies) -> APIRouter:
    router = APIRouter(prefix="/api/v1/comic", tags=["Comic Video"])

    def owned_project(request: Request, job_id: str) -> tuple[dict[str, Any], ComicProject]:
        job = deps.job_for_request(request, job_id)
        if not is_comic_job(job):
            raise HTTPException(404, "Không tìm thấy dự án truyện")
        owner = str(job.get("owner_user_id") or "__legacy__")
        return job, ComicProject(deps.project_root(owner, job_id))

    def editable_project(request: Request, job_id: str) -> tuple[dict[str, Any], ComicProject, Storyboard]:
        job, project = owned_project(request, job_id)
        if job.get("status") in ACTIVE_STATUSES:
            raise HTTPException(409, "Dự án đang được xử lý; hãy chờ xong hoặc hủy trước khi sửa")
        if not project.has_storyboard():
            raise HTTPException(409, "Storyboard chưa được tạo")
        return job, project, project.load_storyboard()

    def save(project: ComicProject, data: dict[str, Any]) -> Storyboard:
        try:
            storyboard = Storyboard.model_validate(data)
        except ValidationError as exc:
            raise HTTPException(422, _validation_detail(exc)) from exc
        return project.save_storyboard(storyboard)

    def project_payload(
        job_id: str,
        job: dict[str, Any],
        project: ComicProject,
        request: Request | None = None,
        *,
        focus_scene: int | None = None,
    ) -> dict[str, Any]:
        storyboard = project.load_storyboard() if project.has_storyboard() else None
        manifest = project.load_manifest()
        base = f"/api/v1/comic/projects/{job_id}/media"
        scenes_media = manifest.get("scenes", {})
        shots_media = manifest.get("shots", {})

        all_scenes = list(storyboard.scenes) if storyboard else []
        total_scenes = len(all_scenes)
        total_shots = sum(len(s.shots) for s in all_scenes)

        raw_page = request.query_params.get("scene_page") if request is not None else None
        raw_limit = request.query_params.get("scene_limit") if request is not None else None
        paginate_scenes = bool(raw_page or raw_limit)
        if paginate_scenes:
            try:
                scene_limit = max(1, min(int(raw_limit or 6), 120))
            except ValueError:
                scene_limit = 6
            try:
                scene_page = max(1, int(raw_page or 1))
            except ValueError:
                scene_page = 1
            if focus_scene is not None and focus_scene >= 1:
                scene_page = (focus_scene - 1) // scene_limit + 1
            total_pages = max(1, (total_scenes + scene_limit - 1) // scene_limit)
            scene_page = min(scene_page, total_pages)
            start = (scene_page - 1) * scene_limit
            end = start + scene_limit
            visible_scenes = all_scenes[start:end]
        else:
            scene_limit = max(1, total_scenes)
            scene_page = 1
            total_pages = 1
            start = 0
            visible_scenes = all_scenes

        visible_indices = {str(scene.scene_index) for scene in visible_scenes}
        visible_shot_labels = {
            f"{scene.scene_index}.{shot.shot_index}"
            for scene in visible_scenes
            for shot in scene.shots
        }
        storyboard_dump = storyboard.model_dump(mode="json") if storyboard else None
        if storyboard_dump is not None and paginate_scenes:
            storyboard_dump["scenes"] = storyboard_dump["scenes"][start : start + scene_limit]

        def urls(kind: str, field: str, entries: dict[str, Any]) -> dict[str, str]:
            return {
                label: f"{base}/{kind}/{entry[field]}"
                for label, entry in entries.items()
                if entry.get(field) and (not paginate_scenes or label in visible_shot_labels)
            }

        payload: dict[str, Any] = {
            "job": deps.job_response(job_id, job),
            "settings": (job.get("resume_request") or {}).get("settings"),
            "storyboard": storyboard_dump,
            "scenes_pagination": {
                "page": scene_page,
                "limit": scene_limit,
                "offset": start,
                "total_scenes": total_scenes,
                "total_pages": total_pages,
                "total_shots": total_shots,
            },
            "rendered_revision": manifest.get("revision"),
            "media": {
                "scenes": {
                    index: f"{base}/images/{entry['image']}"
                    for index, entry in scenes_media.items()
                    if entry.get("image") and (not paginate_scenes or index in visible_indices)
                },
                "references": {cid: f"{base}/refs/{name}" for cid, name in manifest.get("references", {}).items()},
                "uploads": {
                    str(scene.scene_index): f"{base}/uploads/{scene.image_override}"
                    for scene in visible_scenes
                    if scene.image_override
                },
                "keyframes": urls("keyframes", "keyframe", shots_media),
                "animations": urls("animations", "animation", shots_media),
                "previews": urls("clips", "clip", manifest.get("previews", {})),
                "keyframe_uploads": {
                    f"{scene.scene_index}.{shot.shot_index}": f"{base}/uploads/{shot.keyframe_override}"
                    for scene in visible_scenes
                    for shot in scene.shots
                    if shot.keyframe_override
                },
            },
            # Per-shot generation report: provider, QC, fallback, seed, cost.
            "shots": {
                label: {k: v for k, v in entry.items() if k != "cues"}
                for label, entry in shots_media.items()
                if not paginate_scenes or label in visible_shot_labels
            },
            "metrics": manifest.get("metrics"),
        }
        return payload

    def locate_shot(storyboard: Storyboard, scene_index: int, shot_index: int) -> tuple[int, int]:
        try:
            scene = storyboard.scene(scene_index)
            return storyboard.scenes.index(scene), scene.shots.index(scene.shot(shot_index))
        except KeyError:
            raise HTTPException(404, "Không có shot này") from None

    @router.get("/options")
    def options(request: Request) -> JSONResponse:
        account = deps.current_account(request)
        return JSONResponse({
            "styles": [{"key": key, "label": label} for key, (label, _prompt) in COMIC_STYLES.items()],
            "aspect_ratios": list(ASPECT_RATIOS),
            "camera_motions": list(CAMERA_MOTIONS),
            "languages": [{"key": code, "label": profile.label_vi} for code, profile in LANGUAGES.items()],
            "llm_providers": annotate_providers(account, "comic_llm", llm_provider_statuses()),
            "image_providers": annotate_providers(account, "comic_image", image_provider_statuses()),
            "animation_providers": annotate_providers(account, "comic_animation", animation_provider_statuses()),
            "tts_providers": [
                {"key": s.key, "label": s.label, "configured": s.configured, "voices": s.voices}
                for s in provider_statuses() if s.key in ("edge", "cit")
            ],
            "shot_types": list(SHOT_TYPES), "shot_movements": list(SHOT_MOVEMENTS), "shot_angles": list(SHOT_ANGLES),
            "max_story_chars": MAX_STORY_CHARS,
        })

    @router.post("/projects", status_code=202)
    async def create_project(
        request: Request,
        title: str = Form("Truyện chưa đặt tên"),
        text: str = Form(""),
        file: UploadFile | None = File(default=None),
        background_music: UploadFile | None = File(default=None),
        language: str = Form("vi"),
        style: str = Form("anime_comic"),
        aspect_ratio: str = Form("9:16"),
        llm_provider: str = Form("auto"),
        image_provider: str = Form("placeholder"),
        narrator_voice: str = Form("female"),
        # Empty means CIT Voice Studio when its app is running, otherwise Edge TTS.
        tts_provider: str = Form(""),
        cit_narrator_voice: str = Form(""),
        cit_female_voice: str = Form(""),
        cit_male_voice: str = Form(""),
        speech_rate: float = Form(1.0),
        target_scene_duration: float = Form(7.0),
        max_scenes: int = Form(40),
        burn_subtitles: str = Form("true"),
        music_volume: float = Form(0.15),
        seed: int = Form(0),
        auto_render: str = Form("true"),
        video_mode: str = Form("comic"),
        animation_provider: str = Form("camera_2_5d"),
        quality: str = Form("final"),
        fps: int = Form(30),
        motion_strength: float = Form(0.45),
        max_animation_attempts: int = Form(2),
        animation_budget_seconds: float = Form(240.0),
    ) -> JSONResponse:
        account = deps.current_account(request)
        owner = str(account["user_id"])
        if llm_provider == "auto" and not has_paid_features(account):
            # "Auto" prefers Gemini; free plans fall back to the next engine in its chain.
            qwen3 = next((s for s in llm_provider_statuses() if s["key"] == "qwen3"), None)
            llm_provider = "qwen3" if qwen3 and qwen3["configured"] else "heuristic"
        tts_provider = tts_provider.strip() or default_tts_provider()
        tts_voices: dict[str, str] = {}
        if tts_provider == "cit":
            try:
                ensure_provider_configured("cit")
                tts_voices = resolve_tts_voice_selection("cit", cit_female_voice, cit_male_voice)
                narrator_gender = "male" if narrator_voice == "male" else "female"
                narrator = resolve_tts_voice_selection(
                    "cit", *((cit_narrator_voice, "") if narrator_gender == "female" else ("", cit_narrator_voice))
                )
                tts_voices["narrator"] = narrator.get(narrator_gender, "")
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        try:
            settings = ComicSettings(
                title=title.strip() or "Truyện chưa đặt tên", language=language, style=style,
                aspect_ratio=aspect_ratio, llm_provider=llm_provider, image_provider=image_provider,
                narrator_voice=narrator_voice, tts_provider=tts_provider, tts_voices=tts_voices,
                speech_rate=speech_rate,
                target_scene_duration=target_scene_duration, max_scenes=max_scenes,
                burn_subtitles=form_flag(burn_subtitles), music_volume=music_volume, seed=seed,
                auto_render=form_flag(auto_render), video_mode=video_mode, animation_provider=animation_provider,
                quality=quality, fps=fps, motion_strength=motion_strength,
                max_animation_attempts=max_animation_attempts, animation_budget_seconds=animation_budget_seconds,
            )
        except ValidationError as exc:
            raise HTTPException(400, _validation_detail(exc)) from exc
        chosen = {settings.llm_provider, settings.image_provider}
        statuses = [*llm_provider_statuses(), *image_provider_statuses()]
        if settings.video_mode == "animated":
            chosen.add(settings.animation_provider)
            statuses += animation_provider_statuses()
        unavailable = [s["label"] for s in statuses if s["key"] in chosen and not s["configured"]]
        if unavailable:
            raise HTTPException(400, "Chưa cấu hình: " + ", ".join(unavailable))
        try:
            ensure_comic_settings_allowed(account, settings.model_dump(mode="json"))
        except PlanEntitlementError as exc:
            raise HTTPException(403, str(exc)) from exc
        try:
            if file is not None and file.filename:
                story = read_story_upload(file.filename, await file.read(5 * 1024 * 1024 + 1))
            else:
                story = normalize_story_text(text)
        except StoryTextError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not story.strip():
            raise HTTPException(400, "Hãy nhập nội dung truyện hoặc tải file TXT/DOCX")
        if len(story) > MAX_STORY_CHARS:
            raise HTTPException(400, f"Truyện dài {len(story):,} ký tự; giới hạn mỗi dự án là {MAX_STORY_CHARS:,}")

        music_data = b""
        music_ext = ""
        if background_music is not None and background_music.filename:
            music_ext = Path(background_music.filename).suffix.lower()
            if music_ext not in MUSIC_EXTENSIONS:
                raise HTTPException(400, "Nhạc nền phải là mp3, m4a, aac, wav hoặc ogg")
            music_data = await background_music.read(MAX_MUSIC_UPLOAD_BYTES + 1)
            if len(music_data) > MAX_MUSIC_UPLOAD_BYTES:
                raise HTTPException(400, "Nhạc nền vượt quá 30 MB")

        job_id = uuid.uuid4().hex[:8]
        deps.reserve_quota(job_id, account)
        try:
            project = ComicProject(deps.project_root(owner, job_id))
            project.write_story(story)
            music_path = None
            if music_data:
                music_path = deps.upload_dir(owner, job_id) / f"music{music_ext}"
                music_path.write_bytes(music_data)
            resume_request = {
                "settings": settings.model_dump(mode="json"),
                "music_path": str(music_path) if music_path else None,
                "render": settings.auto_render,
            }
            deps.create_job(job_id, {
                "status": "queued", "step": "queued", "progress": 0.0,
                "job_action": COMIC_JOB_ACTION, "job_type": COMIC_JOB_ACTION,
                "filename": settings.title, "target_language": settings.language,
                "comic": comic_summary(settings), "resume_request": resume_request,
            }, owner)
        except Exception:
            deps.release_quota(job_id)
            shutil.rmtree(deps.project_root(owner, job_id), ignore_errors=True)
            raise
        deps.notify_scheduler()
        return JSONResponse({"job_id": job_id, "status": "queued", "story_chars": len(story)}, status_code=202)

    @router.get("/projects")
    def list_projects_endpoint(
        request: Request,
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=6, ge=1, le=100),
        offset: int | None = Query(default=None, ge=0),
        search: str | None = Query(default=None),
    ) -> JSONResponse:
        if deps.list_projects is None:
            return JSONResponse({"items": [], "total": 0, "page": 1, "limit": limit, "offset": 0, "total_pages": 1})
        resolved_offset = offset if offset is not None else (page - 1) * limit
        items, total = deps.list_projects(request, limit=limit, offset=resolved_offset, search=search)
        total_pages = max(1, (total + limit - 1) // limit)
        if offset is None and page > total_pages and total > 0:
            page = total_pages
            resolved_offset = (page - 1) * limit
            items, total = deps.list_projects(request, limit=limit, offset=resolved_offset, search=search)
        resolved_page = page if offset is None else (resolved_offset // limit) + 1
        return JSONResponse({
            "items": items,
            "total": total,
            "page": resolved_page,
            "limit": limit,
            "offset": resolved_offset,
            "total_pages": total_pages,
        })

    @router.get("/projects/{job_id}")
    def get_project(
        request: Request,
        job_id: str,
        scene_page: int | None = Query(default=None, ge=1),
        scene_limit: int | None = Query(default=None, ge=1, le=120),
    ) -> JSONResponse:
        _ = (scene_page, scene_limit)
        job, project = owned_project(request, job_id)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.get("/projects/{job_id}/story")
    def get_story(request: Request, job_id: str) -> JSONResponse:
        _job, project = owned_project(request, job_id)
        return JSONResponse({"text": project.read_story()})

    @router.patch("/projects/{job_id}/scenes/{scene_index}")
    def patch_scene(request: Request, job_id: str, scene_index: int, patch: ScenePatch) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        try:
            scene = storyboard.scene(scene_index)
        except KeyError:
            raise HTTPException(404, "Không có cảnh này") from None
        changes = patch.model_dump(exclude_none=True, mode="json")
        if changes:
            data = storyboard.model_dump(mode="json")
            target = data["scenes"][storyboard.scenes.index(scene)]
            target.update(changes)
            target["version"] = scene.version + 1
            save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.post("/projects/{job_id}/scenes")
    def insert_scene(request: Request, job_id: str, body: SceneInsert) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        data = storyboard.model_dump(mode="json")
        position = min(body.after, len(data["scenes"]))
        new_scene = ComicScene(scene_index=position + 1, narration=body.narration, visual_prompt=body.visual_prompt)
        data["scenes"].insert(position, new_scene.model_dump(mode="json"))
        save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request, focus_scene=position + 1))

    @router.delete("/projects/{job_id}/scenes/{scene_index}")
    def delete_scene(request: Request, job_id: str, scene_index: int) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        if len(storyboard.scenes) == 1:
            raise HTTPException(409, "Storyboard phải còn ít nhất một cảnh")
        data = storyboard.model_dump(mode="json")
        data["scenes"] = [s for s in data["scenes"] if s["scene_index"] != scene_index]
        if len(data["scenes"]) == len(storyboard.scenes):
            raise HTTPException(404, "Không có cảnh này")
        save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.patch("/projects/{job_id}/characters/{character_id}")
    def patch_character(request: Request, job_id: str, character_id: str, patch: CharacterPatch) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        character = storyboard.character(character_id)
        if character is None:
            raise HTTPException(404, "Không có nhân vật này")
        data = storyboard.model_dump(mode="json")
        index = storyboard.characters.index(character)
        updated = {**data["characters"][index], **patch.model_dump(exclude_none=True)}
        try:
            data["characters"][index] = ComicCharacter.model_validate({**updated, "id": character.id}).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(422, _validation_detail(exc)) from exc
        save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.post("/projects/{job_id}/scenes/{scene_index}/image")
    async def upload_scene_image(request: Request, job_id: str, scene_index: int,
                                 file: UploadFile = File(...)) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        try:
            storyboard.scene(scene_index)
        except KeyError:
            raise HTTPException(404, "Không có cảnh này") from None
        name = read_upload_png(await file.read(MAX_IMAGE_UPLOAD_BYTES + 1), project.dir("uploads"),
                               f"s{scene_index:03d}")
        storyboard_data = storyboard.model_dump(mode="json")
        for scene in storyboard_data["scenes"]:
            if scene["scene_index"] == scene_index:
                scene["image_override"] = name
                scene["version"] += 1
        save(project, storyboard_data)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.delete("/projects/{job_id}/scenes/{scene_index}/image")
    def clear_scene_image(request: Request, job_id: str, scene_index: int) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        data = storyboard.model_dump(mode="json")
        for scene in data["scenes"]:
            if scene["scene_index"] == scene_index and scene.get("image_override"):
                scene["image_override"] = None
                scene["version"] += 1
        save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    # ------------------------------------------------------------------ shots
    @router.patch("/projects/{job_id}/scenes/{scene_index}/shots/{shot_index}")
    def patch_shot(request: Request, job_id: str, scene_index: int, shot_index: int, patch: ShotPatch) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        scene_pos, shot_pos = locate_shot(storyboard, scene_index, shot_index)
        changes = patch.model_dump(exclude_none=True, mode="json")
        if changes:
            data = storyboard.model_dump(mode="json")
            target = data["scenes"][scene_pos]["shots"][shot_pos]
            for nested in ("camera", "animation"):
                if nested in changes:
                    changes[nested] = {**target[nested], **changes[nested]}
            target.update(changes)
            target["version"] += 1
            save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.post("/projects/{job_id}/scenes/{scene_index}/shots")
    def insert_shot(request: Request, job_id: str, scene_index: int, body: ShotInsert) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        if storyboard.mode != "animated":
            raise HTTPException(409, "Chỉ storyboard hoạt hình mới có shot")
        try:
            scene = storyboard.scene(scene_index)
        except KeyError:
            raise HTTPException(404, "Không có cảnh này") from None
        data = storyboard.model_dump(mode="json")
        shots = data["scenes"][storyboard.scenes.index(scene)]["shots"]
        if len(shots) >= 8:
            raise HTTPException(409, "Mỗi cảnh tối đa 8 shot")
        shot = Shot(shot_index=1, keyframe_prompt=body.keyframe_prompt, narration=body.narration,
                    motion_prompt=body.motion_prompt or "subtle natural motion, smooth camera")
        shots.insert(min(body.after, len(shots)), shot.model_dump(mode="json"))
        save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.delete("/projects/{job_id}/scenes/{scene_index}/shots/{shot_index}")
    def delete_shot(request: Request, job_id: str, scene_index: int, shot_index: int) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        scene_pos, shot_pos = locate_shot(storyboard, scene_index, shot_index)
        data = storyboard.model_dump(mode="json")
        if len(data["scenes"][scene_pos]["shots"]) == 1:
            raise HTTPException(409, "Cảnh phải còn ít nhất một shot; hãy xóa cả cảnh")
        del data["scenes"][scene_pos]["shots"][shot_pos]
        save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.post("/projects/{job_id}/scenes/{scene_index}/shots/{shot_index}/regenerate")
    def regenerate_shot(request: Request, job_id: str, scene_index: int, shot_index: int) -> JSONResponse:
        """New take of the motion only: keyframe and voice are kept, the seed changes."""
        job, project, storyboard = editable_project(request, job_id)
        scene_pos, shot_pos = locate_shot(storyboard, scene_index, shot_index)
        data = storyboard.model_dump(mode="json")
        target = data["scenes"][scene_pos]["shots"][shot_pos]
        target["animation"]["take"] += 1
        target["version"] += 1
        save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.post("/projects/{job_id}/scenes/{scene_index}/shots/{shot_index}/keyframe")
    async def upload_keyframe(request: Request, job_id: str, scene_index: int, shot_index: int,
                              file: UploadFile = File(...)) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        scene_pos, shot_pos = locate_shot(storyboard, scene_index, shot_index)
        name = read_upload_png(await file.read(MAX_IMAGE_UPLOAD_BYTES + 1), project.dir("uploads"),
                               f"k{scene_index:03d}-{shot_index:02d}")
        data = storyboard.model_dump(mode="json")
        target = data["scenes"][scene_pos]["shots"][shot_pos]
        target["keyframe_override"] = name
        target["version"] += 1
        save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.delete("/projects/{job_id}/scenes/{scene_index}/shots/{shot_index}/keyframe")
    def clear_keyframe(request: Request, job_id: str, scene_index: int, shot_index: int) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        scene_pos, shot_pos = locate_shot(storyboard, scene_index, shot_index)
        data = storyboard.model_dump(mode="json")
        target = data["scenes"][scene_pos]["shots"][shot_pos]
        if target.get("keyframe_override"):
            target["keyframe_override"] = None
            target["version"] += 1
            save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    # ------------------------------------------------------------- references
    def character_or_404(storyboard: Storyboard, character_id: str) -> ComicCharacter:
        character = storyboard.character(character_id)
        if character is None:
            raise HTTPException(404, "Không có nhân vật này")
        return character

    def set_approved(project: ComicProject, storyboard: Storyboard, character_id: str, name: str | None) -> None:
        data = storyboard.model_dump(mode="json")
        for character in data["characters"]:
            if character["id"] == character_id:
                character["approved_reference"] = name
        save(project, data)

    @router.get("/projects/{job_id}/characters/{character_id}/references")
    def list_references(request: Request, job_id: str, character_id: str) -> JSONResponse:
        _job, project = owned_project(request, job_id)
        if not project.has_storyboard():
            raise HTTPException(409, "Storyboard chưa được tạo")
        character = character_or_404(project.load_storyboard(), character_id)
        refs_dir = project.root / "refs"
        files = sorted(refs_dir.glob(f"{character.id}-*.png"), key=lambda p: p.stat().st_mtime) \
            if refs_dir.is_dir() else []
        base = f"/api/v1/comic/projects/{job_id}/media/refs"
        return JSONResponse({"character_id": character.id, "approved": character.approved_reference, "variants": [
            {"name": path.name, "url": f"{base}/{path.name}", "approved": path.name == character.approved_reference,
             "uploaded": "-upload-" in path.name, "version": version}
            for version, path in enumerate(files, 1)]})

    @router.post("/projects/{job_id}/characters/{character_id}/references")
    async def upload_reference(request: Request, job_id: str, character_id: str,
                               file: UploadFile = File(...)) -> JSONResponse:
        """A user-supplied character sheet becomes a new variant and is approved right away."""
        job, project, storyboard = editable_project(request, job_id)
        character = character_or_404(storyboard, character_id)
        name = read_upload_png(await file.read(MAX_IMAGE_UPLOAD_BYTES + 1), project.dir("refs"),
                               f"{character.id}-upload")
        set_approved(project, storyboard, character.id, name)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.post("/projects/{job_id}/characters/{character_id}/references/{name}/approve")
    def approve_reference(request: Request, job_id: str, character_id: str, name: str) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        character = character_or_404(storyboard, character_id)
        if not name.startswith(f"{character.id}-") or project.asset_path("refs", name) is None:
            raise HTTPException(404, "Không có phiên bản ảnh tham chiếu này")
        set_approved(project, storyboard, character.id, name)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.delete("/projects/{job_id}/characters/{character_id}/references/approval")
    def unpin_reference(request: Request, job_id: str, character_id: str) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        set_approved(project, storyboard, character_or_404(storyboard, character_id).id, None)
        return JSONResponse(project_payload(job_id, job, project, request))

    # ----------------------------------------------------------------- render
    @router.post("/projects/{job_id}/render")
    def render(request: Request, job_id: str, body: RenderRequest | None = None,
               idempotency_key: str | None = Header(default=None, max_length=128)) -> JSONResponse:
        body = body or RenderRequest()
        job, project = owned_project(request, job_id)
        if idempotency_key and job.get("comic_idempotency_key") == idempotency_key and job.get("status") != "failed":
            # A retried client request must not queue (and pay for) the same render twice.
            return JSONResponse({"job_id": job_id, "status": job.get("status"), "duplicate": True})
        if job.get("status") in ACTIVE_STATUSES:
            raise HTTPException(409, "Dự án đang được xử lý")
        try:
            ensure_comic_settings_allowed(
                deps.quota_account(request, job), (job.get("resume_request") or {}).get("settings") or {}
            )
        except PlanEntitlementError as exc:
            raise HTTPException(403, str(exc)) from exc
        if body.shot:
            if not project.has_storyboard() or project.load_storyboard().mode != "animated":
                raise HTTPException(409, "Chỉ xem trước được shot của storyboard hoạt hình")
            scene_index, shot_index = (int(part) for part in body.shot.split("."))
            locate_shot(project.load_storyboard(), scene_index, shot_index)
        if body.reanalyze:
            project.reset_storyboard()
        return JSONResponse(requeue_comic_job(deps, request, job_id, job, render=not body.images_only,
                                              preview_shot=body.shot, idempotency_key=idempotency_key),
                            status_code=202)

    @router.get("/projects/{job_id}/media/{kind}/{name}")
    def media(request: Request, job_id: str, kind: str, name: str) -> FileResponse:
        _job, project = owned_project(request, job_id)
        path = project.asset_path(kind, name)
        if path is None:
            raise HTTPException(404, "Không tìm thấy tài nguyên")
        # Names are content hashes, so a cached copy can never be stale.
        media_type = "video/mp4" if path.suffix == ".mp4" else "image/png"
        return FileResponse(path, media_type=media_type,
                            headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})

    return router
