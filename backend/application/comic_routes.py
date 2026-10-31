from __future__ import annotations

import asyncio
import hashlib
import io
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from application.comic_service import ACTIVE_STATUSES, COMIC_JOB_ACTION, comic_summary, is_comic_job
from domain.comic import (
    GENRE_PRESETS,
    StoryBible,
    ASPECT_RATIOS,
    CAMERA_MOTIONS,
    COMIC_STYLES,
    MAX_STORY_CHARS,
    SHOT_ANGLES,
    SHOT_MOVEMENTS,
    SHOT_TYPES,
    ComicCharacter,
    ComicLocation,
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
from infrastructure.comic_series import ComicSeries, ComicSeriesStore
from infrastructure.image_providers import REFERENCE_PROVIDERS, image_provider_statuses
from infrastructure.llm_providers import LLMError, create_story_llm, llm_provider_statuses
from infrastructure.story_text import StoryTextError, normalize_story_text, read_story_upload
from infrastructure.tts_providers import (
    default_tts_provider,
    ensure_provider_configured,
    provider_statuses,
    resolve_tts_voice_selection,
)
from infrastructure.video_providers import animation_provider_statuses
from pipeline.comic_storyboard import suggest_bible

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
    series_store: ComicSeriesStore | None = None
    # Removes the job and every file it produced (uploads, renders, work directory).
    delete_project: Callable[[str, dict[str, Any]], None] | None = None
    # Whether the user has a logo to stamp; ``apply_logo`` is refused without one.
    has_logo: Callable[[str], bool] | None = None


class ScenePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narration: str | None = None
    dialogue: list[DialogueLine] | None = None
    characters: list[str] | None = None
    location: str | None = None
    # A planned place ("" clears it); its fixed description then drives the image.
    location_id: str | None = None
    emotion: str | None = None
    visual_prompt: str | None = None
    negative_prompt: str | None = None
    camera_motion: str | None = None


class SceneInsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    after: int = Field(ge=0)
    narration: str = Field(min_length=1, max_length=1500)
    visual_prompt: str = Field(min_length=3, max_length=1500)


def _identity_changed(before: ComicCharacter, after: ComicCharacter) -> bool:
    """A pinned reference of the wrong gender or species must not survive the fix."""
    return (before.gender, before.species) != (after.gender, after.species)


class CharacterPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    gender: str | None = None
    species: str | None = None
    age: str | None = None
    appearance: str | None = None
    outfit: str | None = None
    appearance_vi: str | None = None
    outfit_vi: str | None = None
    voice: str | None = None


def retranslate_on_edit(current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """A changed Vietnamese look clears its English twin, which the worker translates again."""
    for english, vietnamese in (("appearance", "appearance_vi"), ("outfit", "outfit_vi"),
                                ("description", "description_vi"), ("world", "world_vi"),
                                ("fix_note", "fix_note_vi")):
        if vietnamese in changes and english not in changes and changes[vietnamese] != current.get(vietnamese):
            changes[english] = ""
    return changes


def parse_bible(value: str) -> StoryBible | None:
    """The reviewed bible sent with a creation form as JSON; empty means "let the AI decide"."""
    if not value.strip():
        return None
    try:
        return StoryBible.model_validate_json(value)
    except ValidationError as exc:
        raise HTTPException(400, f"Hồ sơ nhân vật/bối cảnh không hợp lệ: {_validation_detail(exc)}") from exc


class SeriesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    # The visual plan shared by the next episodes (era, characters, places).
    story_brief: str | None = Field(default=None, max_length=6000)
    world: str | None = Field(default=None, max_length=600)
    world_vi: str | None = Field(default=None, max_length=600)


class SceneFix(BaseModel):
    """A quick fix from the scene card: what was wrong with the image, then redraw it."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["redraw", "characters", "location", "outfit"]
    characters: list[str] | None = Field(default=None, max_length=8)
    location_id: str | None = Field(default=None, max_length=60)
    character_id: str | None = Field(default=None, max_length=60)
    outfit_vi: str | None = Field(default=None, max_length=400)
    # Outfit: change the character in every scene, not just this one.
    apply_to_all: bool = False
    # What else to tell the image model, in Vietnamese (translated before drawing); "" clears it.
    note_vi: str | None = Field(default=None, max_length=400)
    draw_now: bool = True


class BulkScenes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_indexes: list[int] = Field(min_length=1, max_length=400)
    # A planned place for all of them ("" clears it).
    location_id: str | None = Field(default=None, max_length=60)
    redraw: bool = False
    draw_now: bool = False


class LocationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=600)
    description_vi: str | None = Field(default=None, max_length=600)


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
    # With images_only: draw just the first N scenes ("Vẽ thử 3 cảnh") to check the look cheaply,
    # or just these scene numbers.
    preview_scenes: int | None = Field(default=None, ge=1, le=20)
    scenes: list[int] | None = Field(default=None, min_length=1, max_length=40)
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
                      idempotency_key: str | None = None, preview_scenes: int | None = None,
                      preview_scene_indexes: list[int] | None = None) -> dict[str, Any]:
    """Return a finished comic job to the queue; the worker reuses every unchanged asset."""
    resume_request = dict(job.get("resume_request") or {})
    resume_request["render"] = render
    resume_request["preview_shot"] = preview_shot
    resume_request["preview_scenes"] = preview_scenes if not render else None
    resume_request["preview_scene_indexes"] = preview_scene_indexes if not render else None
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


def settings_form(
    # Empty: "Truyện chưa đặt tên" for a story, "Tập N" for an episode.
    title: str = Form(""),
    youtube_title: str = Form(""),
    language: str = Form("vi"),
    style: str = Form("manhua"),
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
    max_scenes: int | None = Form(None),
    burn_subtitles: str = Form("true"),
    music_volume: float = Form(0.15),
    seed: int = Form(0),
    auto_render: str = Form("true"),
    video_mode: str = Form("comic"),
    animation_provider: str = Form("camera_2_5d"),
    quality: str = Form("final"),
    fps: int = Form(24),
    motion_strength: float = Form(0.45),
    max_animation_attempts: int = Form(2),
    animation_budget_seconds: float = Form(240.0),
    # Visual plan: era and world, each character's look, the main places (any language).
    story_brief: str = Form("", max_length=6000),
    # Genre preset key and the bible the user reviewed (JSON from /bible/suggest, edited).
    genre: str = Form("", max_length=40),
    bible: str = Form(""),
    apply_logo: str = Form("false"),
) -> dict[str, Any]:
    """Creation-form fields shared by standalone projects and series."""
    return dict(locals())


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
        storyboard_places = list(storyboard.locations) if storyboard else []
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
                # A pinned picture shows even before the next render; otherwise the last drawn one.
                "location_references": {
                    **{place_id: f"{base}/refs/{name}"
                       for place_id, name in manifest.get("location_references", {}).items()},
                    **{place.id: f"{base}/refs/{place.approved_reference}" for place in storyboard_places
                       if place.approved_reference and project.asset_path("refs", place.approved_reference)},
                },
                "location_drawn": manifest.get("location_references", {}),
                "place_references_supported": (
                    ((job.get("resume_request") or {}).get("settings") or {}).get("image_provider")
                    in REFERENCE_PROVIDERS),
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
            "genres": [{"key": key, **preset} for key, preset in GENRE_PRESETS.items()],
        })

    @router.post("/bible/suggest")
    async def suggest_story_bible(
        request: Request,
        text: str = Form(""),
        file: UploadFile | None = File(default=None),
        story_brief: str = Form("", max_length=6000),
        genre: str = Form("", max_length=40),
        language: str = Form("vi"),
        llm_provider: str = Form("auto"),
        # For a new episode: compare with the series' bible and mark what is new.
        series_id: str = Form(""),
    ) -> JSONResponse:
        """Read the story and propose characters, places and the world for the user to review."""
        account = deps.current_account(request)
        story = await read_story_input(text, file, required=not story_brief.strip())
        provider = resolve_llm_provider(account, llm_provider)
        try:
            settings = ComicSettings(language=language, llm_provider=provider, story_brief=story_brief.strip(),
                                     genre=genre if genre in GENRE_PRESETS else "")
        except ValidationError as exc:
            raise HTTPException(400, _validation_detail(exc)) from exc
        known_characters, known_locations, known_world = [], [], ""
        if series_id.strip():
            series = series_store().load(str(account["user_id"]), series_id.strip())
            if series is None:
                raise HTTPException(404, "Không tìm thấy truyện")
            known_characters, known_locations, known_world = series.characters, series.locations, series.world
        try:
            llm = create_story_llm(provider)
        except LLMError as exc:
            raise HTTPException(503, str(exc)) from exc
        if llm is None:
            raise HTTPException(409, "Cần AI chia cảnh (Gemini hoặc Qwen3) để tự đề xuất nhân vật và bối cảnh. "
                                     "Bạn vẫn có thể bỏ qua bước này.")
        try:
            bible = await asyncio.to_thread(
                suggest_bible, llm, story, settings, known_characters=known_characters,
                known_locations=known_locations, known_world=known_world)
        except (LLMError, ValidationError, ValueError) as exc:
            raise HTTPException(502, f"AI chưa đề xuất được hồ sơ ({str(exc)[:200]}). Hãy thử lại hoặc bỏ qua.") from exc
        known_ids = {c.id for c in known_characters} | {p.id for p in known_locations}
        payload = bible.model_dump(mode="json")
        for item in [*payload["characters"], *payload["locations"]]:
            item["status"] = "known" if item["id"] in known_ids else "new"
        payload["genre"] = settings.genre
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    def resolve_llm_provider(account: dict[str, Any], llm_provider: str) -> str:
        if llm_provider == "auto" and not has_paid_features(account):
            # "Auto" prefers Gemini; free plans fall back to the next engine in its chain.
            qwen3 = next((s for s in llm_provider_statuses() if s["key"] == "qwen3"), None)
            return "qwen3" if qwen3 and qwen3["configured"] else "heuristic"
        return llm_provider

    def build_settings(account: dict[str, Any], fields: dict[str, Any]) -> ComicSettings:
        """Validate the creation form into settings the account may use with configured providers."""
        llm_provider = resolve_llm_provider(account, fields["llm_provider"])
        tts_provider = fields["tts_provider"].strip() or default_tts_provider()
        narrator_voice = fields["narrator_voice"]
        tts_voices: dict[str, str] = {}
        if tts_provider == "cit":
            try:
                ensure_provider_configured("cit")
                tts_voices = resolve_tts_voice_selection("cit", fields["cit_female_voice"], fields["cit_male_voice"])
                narrator_gender = "male" if narrator_voice == "male" else "female"
                narrator = resolve_tts_voice_selection(
                    "cit", *((fields["cit_narrator_voice"], "") if narrator_gender == "female"
                             else ("", fields["cit_narrator_voice"]))
                )
                tts_voices["narrator"] = narrator.get(narrator_gender, "")
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        try:
            settings = ComicSettings(
                title=fields["title"].strip() or "Truyện chưa đặt tên", youtube_title=fields["youtube_title"].strip(),
                language=fields["language"], style=fields["style"],
                aspect_ratio=fields["aspect_ratio"], llm_provider=llm_provider,
                image_provider=fields["image_provider"],
                narrator_voice=narrator_voice, tts_provider=tts_provider, tts_voices=tts_voices,
                speech_rate=fields["speech_rate"],
                target_scene_duration=fields["target_scene_duration"], max_scenes=fields["max_scenes"],
                burn_subtitles=form_flag(fields["burn_subtitles"]), music_volume=fields["music_volume"],
                seed=fields["seed"], auto_render=form_flag(fields["auto_render"]), video_mode=fields["video_mode"],
                animation_provider=fields["animation_provider"], quality=fields["quality"], fps=fields["fps"],
                motion_strength=fields["motion_strength"], max_animation_attempts=fields["max_animation_attempts"],
                animation_budget_seconds=fields["animation_budget_seconds"],
                story_brief=fields["story_brief"].strip(),
                genre=fields["genre"] if fields["genre"] in GENRE_PRESETS else "",
                bible=parse_bible(fields["bible"]),
                apply_logo=form_flag(fields["apply_logo"]),
            )
        except ValidationError as exc:
            raise HTTPException(400, _validation_detail(exc)) from exc
        ensure_settings_usable(account, settings)
        return settings

    def ensure_settings_usable(account: dict[str, Any], settings: ComicSettings) -> None:
        if settings.apply_logo and deps.has_logo is not None and not deps.has_logo(str(account["user_id"])):
            raise HTTPException(400, "Hãy tải logo lên trong trang Tài khoản & logo trước khi chọn chèn logo")
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

    async def read_story_input(text: str, file: UploadFile | None, *, required: bool = True) -> str:
        try:
            if file is not None and file.filename:
                story = read_story_upload(file.filename, await file.read(5 * 1024 * 1024 + 1))
            else:
                story = normalize_story_text(text)
        except StoryTextError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not story.strip():
            if not required:
                return ""
            raise HTTPException(400, "Hãy nhập nội dung truyện hoặc tải file TXT/DOCX")
        if len(story) > MAX_STORY_CHARS:
            raise HTTPException(400, f"Truyện dài {len(story):,} ký tự; giới hạn mỗi dự án là {MAX_STORY_CHARS:,}")
        return story

    async def read_music(background_music: UploadFile | None) -> tuple[bytes, str]:
        if background_music is None or not background_music.filename:
            return b"", ""
        music_ext = Path(background_music.filename).suffix.lower()
        if music_ext not in MUSIC_EXTENSIONS:
            raise HTTPException(400, "Nhạc nền phải là mp3, m4a, aac, wav hoặc ogg")
        music_data = await background_music.read(MAX_MUSIC_UPLOAD_BYTES + 1)
        if len(music_data) > MAX_MUSIC_UPLOAD_BYTES:
            raise HTTPException(400, "Nhạc nền vượt quá 30 MB")
        return music_data, music_ext

    def queue_project(account: dict[str, Any], settings: ComicSettings, story: str,
                      music: tuple[bytes, str], series: dict[str, Any] | None = None,
                      job_id: str | None = None) -> str:
        owner = str(account["user_id"])
        music_data, music_ext = music
        job_id = job_id or uuid.uuid4().hex[:8]
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
            if series:
                resume_request["series"] = series
            deps.create_job(job_id, {
                "status": "queued", "step": "queued", "progress": 0.0,
                "job_action": COMIC_JOB_ACTION, "job_type": COMIC_JOB_ACTION,
                "filename": settings.title, "target_language": settings.language,
                "comic": comic_summary(settings, series=series), "resume_request": resume_request,
            }, owner)
        except Exception:
            deps.release_quota(job_id)
            shutil.rmtree(deps.project_root(owner, job_id), ignore_errors=True)
            raise
        deps.notify_scheduler()
        return job_id

    def queue_episode(account: dict[str, Any], series_id: str, story: str, music: tuple[bytes, str], *,
                      title: str, youtube_title: str, auto_render: bool,
                      episode_brief: str = "", bible: StoryBible | None = None,
                      apply_logo: bool | None = None) -> tuple[str, int]:
        """Queue the next episode of a series with the series' settings and character bible."""
        store = series_store()
        owner = str(account["user_id"])
        series = store.load(owner, series_id)
        if series is None:
            raise HTTPException(404, "Không tìm thấy truyện")
        try:
            settings = ComicSettings.model_validate({
                **series.data.get("settings", {}), "title": title.strip() or "Tập mới",
                "youtube_title": youtube_title.strip(), "auto_render": auto_render,
                "episode_brief": episode_brief.strip(),
                "bible": bible.model_dump(mode="json") if bible is not None else None,
                # None: keep the series' choice; the episode form can override it.
                **({} if apply_logo is None else {"apply_logo": apply_logo}),
            })
        except ValidationError as exc:
            raise HTTPException(400, _validation_detail(exc)) from exc
        ensure_settings_usable(account, settings)
        job_id = uuid.uuid4().hex[:8]
        number = store.add_episode(owner, series.id, job_id)
        if not title.strip():
            settings.title = f"Tập {number}"
        if bible is not None and number > 1:
            def adopt(target: ComicSeries) -> None:
                # Approved for this episode: the series learns them now, even if the episode later fails.
                known = {c.id for c in target.characters}
                target.set_characters([*target.characters, *(c for c in bible.characters if c.id not in known)])
                places = {place.id for place in target.locations}
                target.set_locations([*target.locations, *(p for p in bible.locations if p.id not in places)])

            store.update(owner, series.id, adopt)
        info = {"series_id": series.id, "series_title": series.data.get("title", ""), "episode_number": number}
        try:
            queue_project(account, settings, story, music, info, job_id)
        except Exception:
            store.remove_episode(owner, series.id, job_id)
            raise
        return job_id, number

    @router.post("/projects", status_code=202)
    async def create_project(
        request: Request,
        fields: dict[str, Any] = Depends(settings_form),
        text: str = Form(""),
        file: UploadFile | None = File(default=None),
        background_music: UploadFile | None = File(default=None),
        # Set to add the project as the next episode of that series; its settings then apply.
        series_id: str = Form(""),
        # What is new in this episode: its places and characters (series episodes only).
        episode_brief: str = Form("", max_length=3000),
    ) -> JSONResponse:
        account = deps.current_account(request)
        story = await read_story_input(text, file)
        music = await read_music(background_music)
        if series_id.strip():
            job_id, number = queue_episode(account, series_id.strip(), story, music, title=fields["title"],
                                           youtube_title=fields["youtube_title"],
                                           auto_render=form_flag(fields["auto_render"]),
                                           episode_brief=episode_brief, bible=parse_bible(fields["bible"]),
                                           apply_logo=form_flag(fields["apply_logo"]))
            return JSONResponse({"job_id": job_id, "status": "queued", "story_chars": len(story),
                                 "series_id": series_id.strip(), "episode_number": number}, status_code=202)
        settings = build_settings(account, fields)
        job_id = queue_project(account, settings, story, music)
        return JSONResponse({"job_id": job_id, "status": "queued", "story_chars": len(story)}, status_code=202)

    # ----------------------------------------------------------------- series
    def series_store() -> ComicSeriesStore:
        if deps.series_store is None:
            raise HTTPException(404, "Tính năng truyện nhiều tập chưa được bật")
        return deps.series_store

    def owned_series(request: Request, series_id: str) -> tuple[dict[str, Any], ComicSeries]:
        account = deps.current_account(request)
        series = series_store().load(str(account["user_id"]), series_id)
        if series is None:
            raise HTTPException(404, "Không tìm thấy truyện")
        return account, series

    def series_summary(series: ComicSeries) -> dict[str, Any]:
        settings = series.data.get("settings", {})
        return {
            "series_id": series.id, "title": series.data.get("title", ""),
            "description": series.data.get("description", ""),
            "episodes": len(series.data.get("episodes", [])), "characters": len(series.data.get("characters", [])),
            "style": settings.get("style"), "video_mode": settings.get("video_mode"),
            "aspect_ratio": settings.get("aspect_ratio"),
            "created_at": series.data.get("created_at"), "updated_at": series.data.get("updated_at"),
        }

    def series_payload(request: Request, series: ComicSeries) -> dict[str, Any]:
        base = f"/api/v1/comic/series/{series.id}/media/refs"
        episodes = []
        for entry in series.data.get("episodes", []):
            job = deps.job_for_request(request, str(entry["job_id"]))
            if not is_comic_job(job):
                continue  # Removed by retention or deleted; the series keeps its numbering.
            episodes.append({**deps.job_response(str(entry["job_id"]), job), "episode_number": entry["number"]})
        characters = []
        for character in series.characters:
            ref = series.ref_path(character.approved_reference)
            characters.append({**character.model_dump(mode="json"),
                               "reference_url": f"{base}/{ref.name}" if ref else None})
        return {**series_summary(series), "settings": series.data.get("settings", {}),
                "world": series.world, "world_vi": series.data.get("world_vi", ""),
                "locations": [{**place.model_dump(mode="json"), "reference_url": (
                    f"{base}/{ref.name}" if (ref := series.ref_path(place.approved_reference)) else None)}
                    for place in series.locations],
                "characters": characters, "episode_list": episodes,
                "next_episode_number": series.next_episode_number()}

    @router.post("/series", status_code=201)
    async def create_series(
        request: Request,
        fields: dict[str, Any] = Depends(settings_form),
        description: str = Form("", max_length=20000),
        text: str = Form(""),
        file: UploadFile | None = File(default=None),
        background_music: UploadFile | None = File(default=None),
        episode_title: str = Form(""),
        episode_brief: str = Form("", max_length=3000),
    ) -> JSONResponse:
        """Create a series with the settings every episode shares; story text queues episode 1."""
        account = deps.current_account(request)
        store = series_store()
        settings = build_settings(account, fields)
        story = await read_story_input(text, file, required=False)
        music = await read_music(background_music)
        # The reviewed bible belongs to episode 1; the series keeps what it produces as its own bible.
        stored = settings.model_dump(mode="json", exclude={"title", "youtube_title", "auto_render", "bible"})
        series = store.create(str(account["user_id"]), title=settings.title, description=description.strip(),
                              settings=stored)
        if settings.bible is not None:
            approved = settings.bible

            def seed(target: ComicSeries) -> None:
                # Approved before any episode is analysed: later episodes see it even if episode 1 fails.
                target.set_characters(approved.characters)
                target.set_locations(approved.locations)
                target.data.update(world=approved.world, world_vi=approved.world_vi)

            series = store.update(str(account["user_id"]), series.id, seed)
        result: dict[str, Any] = {"series_id": series.id, "job_id": None}
        if story:
            result["job_id"], result["episode_number"] = queue_episode(
                account, series.id, story, music, title=episode_title, youtube_title=settings.youtube_title,
                auto_render=settings.auto_render, episode_brief=episode_brief, bible=settings.bible)
        return JSONResponse(result, status_code=201)

    @router.get("/series")
    def list_series(request: Request) -> JSONResponse:
        account = deps.current_account(request)
        items = [series_summary(s) for s in series_store().list(str(account["user_id"]))]
        return JSONResponse({"items": items, "total": len(items)})

    @router.get("/series/{series_id}")
    def get_series(request: Request, series_id: str) -> JSONResponse:
        _account, series = owned_series(request, series_id)
        return JSONResponse(series_payload(request, series))

    @router.patch("/series/{series_id}")
    def patch_series(request: Request, series_id: str, patch: SeriesPatch) -> JSONResponse:
        account, series = owned_series(request, series_id)
        changes = retranslate_on_edit(series.data, patch.model_dump(exclude_none=True))
        brief = changes.pop("story_brief", None)

        def apply(target: ComicSeries) -> None:
            target.data.update(changes)
            if brief is not None:
                target.data["settings"] = {**target.data.get("settings", {}), "story_brief": brief.strip()}

        if changes or brief is not None:
            series = series_store().update(str(account["user_id"]), series.id, apply)
        return JSONResponse(series_payload(request, series))

    @router.delete("/series/{series_id}")
    def delete_series(request: Request, series_id: str,
                      with_episodes: bool = Query(default=False)) -> JSONResponse:
        """Delete the series and its files; ``with_episodes`` also deletes every episode video.

        Without it the episodes stay available as standalone projects."""
        account, series = owned_series(request, series_id)
        episodes: list[tuple[str, dict[str, Any]]] = []
        if with_episodes:
            for job_id in series.episode_job_ids():
                job = deps.job_for_request(request, job_id)
                if not is_comic_job(job):
                    continue
                if job.get("status") in ACTIVE_STATUSES:
                    raise HTTPException(409, "Có tập đang được xử lý; hãy dừng tập đó trước khi xóa truyện")
                episodes.append((job_id, job))
        for job_id, job in episodes:
            delete_project(job_id, job)
        series_store().delete(str(account["user_id"]), series.id)
        return JSONResponse({"deleted": True, "series_id": series.id, "deleted_episodes": len(episodes)})

    def delete_project(job_id: str, job: dict[str, Any]) -> None:
        if deps.delete_project is None:
            raise HTTPException(501, "Chưa hỗ trợ xóa dự án")
        deps.delete_project(job_id, job)

    @router.delete("/projects/{job_id}")
    def delete_comic_project(request: Request, job_id: str) -> JSONResponse:
        """Delete a story video permanently, including its files on disk."""
        job, _project = owned_project(request, job_id)
        if job.get("status") in ACTIVE_STATUSES:
            raise HTTPException(409, "Dự án đang được xử lý; hãy dừng trước khi xóa")
        delete_project(job_id, job)
        return JSONResponse({"deleted": True, "job_id": job_id})

    def update_series_character(request: Request, series_id: str, character_id: str,
                                change: Callable[[ComicCharacter, ComicSeries], None]) -> dict[str, Any]:
        account, series = owned_series(request, series_id)
        if series.character(character_id) is None:
            raise HTTPException(404, "Không có nhân vật này")

        def apply(target: ComicSeries) -> None:
            characters = target.characters
            for character in characters:
                if character.id == character_id:
                    change(character, target)
            target.set_characters(characters)

        return series_payload(request, series_store().update(str(account["user_id"]), series.id, apply))

    @router.patch("/series/{series_id}/characters/{character_id}")
    def patch_series_character(request: Request, series_id: str, character_id: str,
                               patch: CharacterPatch) -> JSONResponse:
        def change(character: ComicCharacter, _series: ComicSeries) -> None:
            changes = retranslate_on_edit(character.model_dump(mode="json"), patch.model_dump(exclude_none=True))
            try:
                updated = ComicCharacter.model_validate({**character.model_dump(mode="json"), **changes,
                                                         "id": character.id})
            except ValidationError as exc:
                raise HTTPException(422, _validation_detail(exc)) from exc
            unpin = _identity_changed(character, updated)
            for key in changes:
                setattr(character, key, getattr(updated, key))
            if unpin:
                character.approved_reference = None

        return JSONResponse(update_series_character(request, series_id, character_id, change))

    @router.post("/series/{series_id}/characters/{character_id}/reference")
    async def upload_series_reference(request: Request, series_id: str, character_id: str,
                                      file: UploadFile = File(...)) -> JSONResponse:
        """Pin a reference for every episode rendered from now on (episodes keep their own uploads)."""
        _account, series = owned_series(request, series_id)
        data = await file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
        if series.character(character_id) is None:
            raise HTTPException(404, "Không có nhân vật này")
        name = read_upload_png(data, series.refs_dir, f"{character_id}-upload")

        def change(character: ComicCharacter, _series: ComicSeries) -> None:
            character.approved_reference = name

        return JSONResponse(update_series_character(request, series_id, character_id, change))

    @router.delete("/series/{series_id}/characters/{character_id}/reference")
    def clear_series_reference(request: Request, series_id: str, character_id: str) -> JSONResponse:
        def change(character: ComicCharacter, _series: ComicSeries) -> None:
            character.approved_reference = None

        return JSONResponse(update_series_character(request, series_id, character_id, change))

    @router.get("/series/{series_id}/media/refs/{name}")
    def series_media(request: Request, series_id: str, name: str) -> FileResponse:
        _account, series = owned_series(request, series_id)
        path = series.ref_path(name)
        if path is None:
            raise HTTPException(404, "Không tìm thấy tài nguyên")
        return FileResponse(path, media_type="image/png",
                            headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})

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
        if changes.get("location_id") and storyboard.location(changes["location_id"]) is None:
            raise HTTPException(422, "Không có bối cảnh này trong truyện")
        if changes:
            data = storyboard.model_dump(mode="json")
            target = data["scenes"][storyboard.scenes.index(scene)]
            target.update(changes)
            target["version"] = scene.version + 1
            save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    def queue_scene_images(request: Request, job_id: str, scene_indexes: list[int]) -> dict[str, Any]:
        job = deps.job_for_request(request, job_id)
        try:  # Same plan check as the Render button: a quick fix must not bypass it.
            ensure_comic_settings_allowed(
                deps.quota_account(request, job), (job.get("resume_request") or {}).get("settings") or {})
        except PlanEntitlementError as exc:
            raise HTTPException(403, str(exc)) from exc
        return requeue_comic_job(deps, request, job_id, job, render=False, preview_scene_indexes=scene_indexes)

    @router.post("/projects/{job_id}/scenes/{scene_index}/fix")
    def fix_scene(request: Request, job_id: str, scene_index: int, fix: SceneFix) -> JSONResponse:
        """"Sai nhân vật / Sai bối cảnh / Sai trang phục / Vẽ lại": correct the scene, then redraw only it."""
        job, project, storyboard = editable_project(request, job_id)
        try:
            scene = storyboard.scene(scene_index)
        except KeyError:
            raise HTTPException(404, "Không có cảnh này") from None
        data = storyboard.model_dump(mode="json")
        target = data["scenes"][storyboard.scenes.index(scene)]
        note = fix.note_vi.strip() if fix.note_vi is not None else None
        if fix.kind == "characters":
            unknown = [cid for cid in fix.characters or [] if storyboard.character(cid) is None]
            if unknown:
                raise HTTPException(422, f"Không có nhân vật: {', '.join(unknown)}")
            target["characters"] = list(dict.fromkeys(fix.characters or []))
        elif fix.kind == "location":
            if fix.location_id and storyboard.location(fix.location_id) is None:
                raise HTTPException(422, "Không có bối cảnh này trong truyện")
            target["location_id"] = fix.location_id or ""
        elif fix.kind == "outfit":
            character = storyboard.character(fix.character_id)
            if character is None or not (fix.outfit_vi or "").strip():
                raise HTTPException(422, "Hãy chọn nhân vật và nhập trang phục đúng")
            outfit = fix.outfit_vi.strip()
            if fix.apply_to_all:
                for item in data["characters"]:
                    if item["id"] == character.id:
                        item.update(outfit_vi=outfit, outfit="")  # Translated again before drawing.
            else:
                note = "; ".join(part for part in (note, f"{character.name} mặc {outfit}") if part)
        if note is not None:
            target.update(fix_note_vi=note, fix_note="")
        target["take"] = scene.take + 1
        target["version"] = scene.version + 1
        save(project, data)
        queued = None
        if fix.draw_now:
            queued = queue_scene_images(request, job_id, [scene.scene_index])
            job = deps.job_for_request(request, job_id)
        return JSONResponse({**project_payload(job_id, job, project, request, focus_scene=scene.scene_index),
                             "queued": queued})

    @router.post("/projects/{job_id}/scenes/bulk")
    def bulk_scenes(request: Request, job_id: str, body: BulkScenes) -> JSONResponse:
        """Change the place of many scenes at once, and/or redraw them."""
        job, project, storyboard = editable_project(request, job_id)
        if body.location_id and storyboard.location(body.location_id) is None:
            raise HTTPException(422, "Không có bối cảnh này trong truyện")
        chosen = set(body.scene_indexes)
        data = storyboard.model_dump(mode="json")
        changed: list[int] = []
        for scene, target in zip(storyboard.scenes, data["scenes"]):
            if scene.scene_index not in chosen:
                continue
            if body.location_id is not None:
                target["location_id"] = body.location_id
            if body.redraw:
                target["take"] = scene.take + 1
            target["version"] = scene.version + 1
            changed.append(scene.scene_index)
        if not changed:
            raise HTTPException(404, "Không có cảnh nào trong danh sách")
        save(project, data)
        queued = queue_scene_images(request, job_id, changed[:40]) if body.draw_now else None
        if queued:
            job = deps.job_for_request(request, job_id)
        return JSONResponse({**project_payload(job_id, job, project, request), "changed": changed, "queued": queued})

    @router.post("/projects/{job_id}/scenes")
    def insert_scene(request: Request, job_id: str, body: SceneInsert) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        data = storyboard.model_dump(mode="json")
        position = min(body.after, len(data["scenes"]))
        new_scene = ComicScene(scene_index=position + 1, narration=body.narration, visual_prompt=body.visual_prompt)
        if storyboard.mode == "animated":
            # Animated scenes need at least one shot; start with one covering the whole scene.
            new_scene.shots = [Shot(shot_index=1, keyframe_prompt=body.visual_prompt, narration=body.narration,
                                    motion_prompt="subtle natural motion, smooth camera")]
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
        changes = retranslate_on_edit(data["characters"][index], patch.model_dump(exclude_none=True))
        updated = {**data["characters"][index], **changes}
        try:
            fixed = ComicCharacter.model_validate({**updated, "id": character.id})
        except ValidationError as exc:
            raise HTTPException(422, _validation_detail(exc)) from exc
        if _identity_changed(character, fixed):
            fixed.approved_reference = None
        data["characters"][index] = fixed.model_dump(mode="json")
        save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.patch("/projects/{job_id}/locations/{location_id}")
    def patch_location(request: Request, job_id: str, location_id: str, patch: LocationPatch) -> JSONResponse:
        """Fix how a place looks; every scene set there is redrawn on the next render."""
        job, project, storyboard = editable_project(request, job_id)
        place = storyboard.location(location_id)
        if place is None:
            raise HTTPException(404, "Không có bối cảnh này")
        data = storyboard.model_dump(mode="json")
        index = storyboard.locations.index(place)
        try:
            changes = retranslate_on_edit(data["locations"][index], patch.model_dump(exclude_none=True))
            fixed = ComicLocation.model_validate({**data["locations"][index], **changes, "id": place.id})
        except ValidationError as exc:
            raise HTTPException(422, _validation_detail(exc)) from exc
        data["locations"][index] = fixed.model_dump(mode="json")
        save(project, data)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.patch("/series/{series_id}/locations/{location_id}")
    def patch_series_location(request: Request, series_id: str, location_id: str,
                              patch: LocationPatch) -> JSONResponse:
        account, series = owned_series(request, series_id)
        if not any(place.id == location_id for place in series.locations):
            raise HTTPException(404, "Không có bối cảnh này")

        def apply(target: ComicSeries) -> None:
            target.set_locations([
                ComicLocation.model_validate({**place.model_dump(), **retranslate_on_edit(
                    place.model_dump(), patch.model_dump(exclude_none=True))})
                if place.id == location_id else place
                for place in target.locations
            ])

        try:
            series = series_store().update(str(account["user_id"]), series.id, apply)
        except ValidationError as exc:
            raise HTTPException(422, _validation_detail(exc)) from exc
        return JSONResponse(series_payload(request, series))

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

    # ------------------------------------------------------------ place pictures
    def place_or_404(storyboard: Storyboard, location_id: str) -> ComicLocation:
        place = storyboard.location(location_id)
        if place is None:
            raise HTTPException(404, "Không có bối cảnh này")
        return place

    def set_place_picture(project: ComicProject, storyboard: Storyboard, location_id: str, name: str | None) -> None:
        data = storyboard.model_dump(mode="json")
        for place in data["locations"]:
            if place["id"] == location_id:
                place["approved_reference"] = name
        save(project, data)

    @router.get("/projects/{job_id}/locations/{location_id}/references")
    def list_place_pictures(request: Request, job_id: str, location_id: str) -> JSONResponse:
        _job, project = owned_project(request, job_id)
        if not project.has_storyboard():
            raise HTTPException(409, "Storyboard chưa được tạo")
        place = place_or_404(project.load_storyboard(), location_id)
        refs_dir = project.root / "refs"
        files = sorted(refs_dir.glob(f"place-{place.id}-*.png"), key=lambda p: p.stat().st_mtime) \
            if refs_dir.is_dir() else []
        base = f"/api/v1/comic/projects/{job_id}/media/refs"
        return JSONResponse({"location_id": place.id, "approved": place.approved_reference, "variants": [
            {"name": path.name, "url": f"{base}/{path.name}", "approved": path.name == place.approved_reference,
             "uploaded": "-upload-" in path.name, "version": version}
            for version, path in enumerate(files, 1)]})

    @router.post("/projects/{job_id}/locations/{location_id}/references")
    async def upload_place_picture(request: Request, job_id: str, location_id: str,
                                   file: UploadFile = File(...)) -> JSONResponse:
        """A user's own picture of the place is pinned right away."""
        job, project, storyboard = editable_project(request, job_id)
        place = place_or_404(storyboard, location_id)
        name = read_upload_png(await file.read(MAX_IMAGE_UPLOAD_BYTES + 1), project.dir("refs"),
                               f"place-{place.id}-upload")
        set_place_picture(project, storyboard, place.id, name)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.post("/projects/{job_id}/locations/{location_id}/references/{name}/approve")
    def approve_place_picture(request: Request, job_id: str, location_id: str, name: str) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        place = place_or_404(storyboard, location_id)
        if not name.startswith(f"place-{place.id}-") or project.asset_path("refs", name) is None:
            raise HTTPException(404, "Không có ảnh bối cảnh này")
        set_place_picture(project, storyboard, place.id, name)
        return JSONResponse(project_payload(job_id, job, project, request))

    @router.delete("/projects/{job_id}/locations/{location_id}/references/approval")
    def unpin_place_picture(request: Request, job_id: str, location_id: str) -> JSONResponse:
        job, project, storyboard = editable_project(request, job_id)
        set_place_picture(project, storyboard, place_or_404(storyboard, location_id).id, None)
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
            # Clear the old UI's implicit 40-scene cap while rebuilding legacy projects.
            resume = dict(job.get("resume_request") or {})
            settings_data = dict(resume.get("settings") or {})
            if settings_data.get("max_scenes") == 40:
                settings_data["max_scenes"] = None
                resume["settings"] = settings_data
                job = {**job, "resume_request": resume}
        return JSONResponse(requeue_comic_job(deps, request, job_id, job, render=not body.images_only,
                                              preview_shot=body.shot, idempotency_key=idempotency_key,
                                              preview_scenes=body.preview_scenes if body.images_only else None,
                                              preview_scene_indexes=body.scenes if body.images_only else None),
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
