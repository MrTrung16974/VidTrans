from __future__ import annotations

import hashlib
import io
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from application.comic_service import ACTIVE_STATUSES, COMIC_JOB_ACTION, comic_summary, is_comic_job
from domain.comic import (
    ASPECT_RATIOS,
    CAMERA_MOTIONS,
    COMIC_STYLES,
    MAX_STORY_CHARS,
    ComicCharacter,
    ComicScene,
    ComicSettings,
    DialogueLine,
    Storyboard,
)
from domain.languages import LANGUAGES
from infrastructure.comic_project import ComicProject
from infrastructure.image_providers import image_provider_statuses
from infrastructure.llm_providers import llm_provider_statuses
from infrastructure.story_text import StoryTextError, normalize_story_text, read_story_upload

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


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    images_only: bool = False
    reanalyze: bool = False


def form_flag(value: str) -> bool:
    # Explicit parsing: an absent checkbox must keep the default, not silently become False.
    return str(value).strip().lower() not in {"0", "false", "off", "no", ""}


def _validation_detail(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'storyboard'}: {e['msg']}" for e in exc.errors())[:600]


def requeue_comic_job(deps: ComicRouteDependencies, request: Request, job_id: str, job: dict[str, Any],
                      *, render: bool) -> dict[str, Any]:
    """Return a finished comic job to the queue; the worker reuses every unchanged asset."""
    resume_request = dict(job.get("resume_request") or {})
    resume_request["render"] = render
    deps.reserve_quota(job_id, deps.quota_account(request, job))
    try:
        deps.retry_job(
            job_id, step="queued", progress=0.0, error=None, error_message=None, step_detail=None,
            cancel_requested=0, worker_id=None, started_at=None, finished_at=None, completed_at=None,
            resume_request=resume_request,
        )
    except Exception:
        deps.release_quota(job_id)
        raise
    deps.notify_scheduler()
    return {"job_id": job_id, "status": "queued", "render": render}


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

    def project_payload(job_id: str, job: dict[str, Any], project: ComicProject) -> dict[str, Any]:
        storyboard = project.load_storyboard() if project.has_storyboard() else None
        manifest = project.load_manifest()
        base = f"/api/v1/comic/projects/{job_id}/media"
        scenes_media = manifest.get("scenes", {})
        payload: dict[str, Any] = {
            "job": deps.job_response(job_id, job),
            "settings": (job.get("resume_request") or {}).get("settings"),
            "storyboard": storyboard.model_dump(mode="json") if storyboard else None,
            "rendered_revision": manifest.get("revision"),
            "media": {
                "scenes": {
                    index: f"{base}/images/{entry['image']}" for index, entry in scenes_media.items()
                    if entry.get("image")
                },
                "references": {cid: f"{base}/refs/{name}" for cid, name in manifest.get("references", {}).items()},
                "uploads": {
                    str(scene.scene_index): f"{base}/uploads/{scene.image_override}"
                    for scene in (storyboard.scenes if storyboard else []) if scene.image_override
                },
            },
        }
        return payload

    @router.get("/options")
    def options() -> JSONResponse:
        return JSONResponse({
            "styles": [{"key": key, "label": label} for key, (label, _prompt) in COMIC_STYLES.items()],
            "aspect_ratios": list(ASPECT_RATIOS),
            "camera_motions": list(CAMERA_MOTIONS),
            "languages": [{"key": code, "label": profile.label_vi} for code, profile in LANGUAGES.items()],
            "llm_providers": llm_provider_statuses(),
            "image_providers": image_provider_statuses(),
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
        speech_rate: float = Form(1.0),
        target_scene_duration: float = Form(7.0),
        max_scenes: int = Form(40),
        burn_subtitles: str = Form("true"),
        music_volume: float = Form(0.15),
        seed: int = Form(0),
        auto_render: str = Form("true"),
    ) -> JSONResponse:
        account = deps.current_account(request)
        owner = str(account["user_id"])
        try:
            settings = ComicSettings(
                title=title.strip() or "Truyện chưa đặt tên", language=language, style=style,
                aspect_ratio=aspect_ratio, llm_provider=llm_provider, image_provider=image_provider,
                narrator_voice=narrator_voice, speech_rate=speech_rate,
                target_scene_duration=target_scene_duration, max_scenes=max_scenes,
                burn_subtitles=form_flag(burn_subtitles), music_volume=music_volume, seed=seed,
                auto_render=form_flag(auto_render),
            )
        except ValidationError as exc:
            raise HTTPException(400, _validation_detail(exc)) from exc
        unavailable = [s["label"] for s in [*llm_provider_statuses(), *image_provider_statuses()]
                       if s["key"] in {settings.llm_provider, settings.image_provider} and not s["configured"]]
        if unavailable:
            raise HTTPException(400, "Chưa cấu hình: " + ", ".join(unavailable))
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

    @router.get("/projects/{job_id}")
    def get_project(request: Request, job_id: str) -> JSONResponse:
        job, project = owned_project(request, job_id)
        return JSONResponse(project_payload(job_id, job, project))

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
        return JSONResponse(project_payload(job_id, job, project))

    @router.post("/projects/{job_id}/scenes")
    def insert_scene(request: Request, job_id: str, body: SceneInsert) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        data = storyboard.model_dump(mode="json")
        position = min(body.after, len(data["scenes"]))
        new_scene = ComicScene(scene_index=position + 1, narration=body.narration, visual_prompt=body.visual_prompt)
        data["scenes"].insert(position, new_scene.model_dump(mode="json"))
        save(project, data)
        return JSONResponse(project_payload(job_id, job, project))

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
        return JSONResponse(project_payload(job_id, job, project))

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
        return JSONResponse(project_payload(job_id, job, project))

    @router.post("/projects/{job_id}/scenes/{scene_index}/image")
    async def upload_scene_image(request: Request, job_id: str, scene_index: int,
                                 file: UploadFile = File(...)) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        try:
            storyboard.scene(scene_index)
        except KeyError:
            raise HTTPException(404, "Không có cảnh này") from None
        data = await file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
        if len(data) > MAX_IMAGE_UPLOAD_BYTES:
            raise HTTPException(400, "Ảnh vượt quá 15 MB")
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                if image.width * image.height > 40_000_000:
                    raise HTTPException(400, "Ảnh quá lớn")
                name = f"s{scene_index:03d}-{hashlib.sha256(data).hexdigest()[:16]}.png"
                path = project.dir("uploads") / name
                image.convert("RGB").save(path, format="PNG")
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(400, "File không phải ảnh hợp lệ (PNG, JPG, WEBP)") from exc
        storyboard_data = storyboard.model_dump(mode="json")
        for scene in storyboard_data["scenes"]:
            if scene["scene_index"] == scene_index:
                scene["image_override"] = name
                scene["version"] += 1
        save(project, storyboard_data)
        return JSONResponse(project_payload(job_id, job, project))

    @router.delete("/projects/{job_id}/scenes/{scene_index}/image")
    def clear_scene_image(request: Request, job_id: str, scene_index: int) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        data = storyboard.model_dump(mode="json")
        for scene in data["scenes"]:
            if scene["scene_index"] == scene_index and scene.get("image_override"):
                scene["image_override"] = None
                scene["version"] += 1
        save(project, data)
        return JSONResponse(project_payload(job_id, job, project))

    @router.post("/projects/{job_id}/render")
    def render(request: Request, job_id: str, body: RenderRequest | None = None) -> JSONResponse:
        body = body or RenderRequest()
        job, project = owned_project(request, job_id)
        if job.get("status") in ACTIVE_STATUSES:
            raise HTTPException(409, "Dự án đang được xử lý")
        if body.reanalyze:
            project.reset_storyboard()
        return JSONResponse(requeue_comic_job(deps, request, job_id, job, render=not body.images_only),
                            status_code=202)

    @router.get("/projects/{job_id}/media/{kind}/{name}")
    def media(request: Request, job_id: str, kind: str, name: str) -> FileResponse:
        _job, project = owned_project(request, job_id)
        path = project.asset_path(kind, name)
        if path is None:
            raise HTTPException(404, "Không tìm thấy ảnh")
        # Names are content hashes, so a cached copy can never be stale.
        return FileResponse(path, media_type="image/png",
                            headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})

    return router
