"""HTTP API of account branding: the logo/settings page and logo jobs.

    /api/v1/me/branding            GET, PUT   settings of the signed-in account
    /api/v1/me/branding/logo       GET, POST, DELETE
    /api/v1/me/branding/template   GET, POST, DELETE  sample of the old logo to replace
    /api/v1/branding/videos        POST (queue a video), GET (history)
    /api/v1/branding/videos/{id}   GET; /download; /reprocess

Every lookup is scoped to the caller: settings and logos by user id, jobs through
the shared ``job_for_request`` ownership check.
"""
from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from application.branding_service import BRANDING_JOB_ACTION, is_branding_job
from domain.branding import LOGO_POSITIONS, BrandingConfig, PlacementOverride
from infrastructure.branding_store import BrandingError, BrandingStore

NO_STORE = {"Cache-Control": "no-store"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
# Same ceiling as nginx (client_max_body_size 2048m); keeps oversized uploads off the disk.
MAX_VIDEO_BYTES = 2048 * 1024 * 1024


@dataclass
class BrandingRouteDependencies:
    store: BrandingStore
    current_account: Callable[[Request], dict[str, Any]]
    update_display_name: Callable[[str, str], dict[str, Any]]
    public_account: Callable[[dict[str, Any]], dict[str, Any]]
    job_for_request: Callable[[Request, str], dict[str, Any] | None]
    job_response: Callable[[str, dict[str, Any]], dict[str, Any]]
    list_jobs: Callable[..., tuple[list[dict[str, Any]], int]]
    create_job: Callable[[str, dict[str, Any], str], dict[str, Any]]
    retry_job: Callable[..., dict[str, Any]]
    reserve_quota: Callable[[str, dict[str, Any]], None]
    release_quota: Callable[[str], None]
    notify_scheduler: Callable[[], None]
    upload_dir: Callable[[str, str], Path]
    validate_video: Callable[[Path], None]
    artifact_path: Callable[[str], Path]


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=120)


class ReprocessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    override: PlacementOverride = "auto"
    cover_original: bool | None = None


def create_branding_router(deps: BrandingRouteDependencies) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["branding"])

    def branding_payload(user_id: str) -> dict[str, Any]:
        stored = deps.store.get(user_id)
        logo = stored["logo"]
        if logo:
            # The content-addressed filename doubles as a cache buster for the preview.
            logo = {**logo, "url": f"/api/v1/me/branding/logo?v={logo['filename']}"}
        template = stored["template"]
        if template:
            template = {**template, "url": f"/api/v1/me/branding/template?v={template['filename']}"}
        return {
            "config": stored["config"].model_dump(mode="json"), "logo": logo, "template": template,
            "updated_at": stored["updated_at"], "positions": list(LOGO_POSITIONS),
        }

    def branding_job(request: Request, job_id: str) -> dict[str, Any]:
        job = deps.job_for_request(request, job_id)
        if not is_branding_job(job):
            raise HTTPException(404, "Không tìm thấy job gắn logo")
        return job

    # ------------------------------------------------------------------ account
    @router.put("/me")
    def update_me(request: Request, body: AccountPatch) -> JSONResponse:
        account = deps.current_account(request)
        try:
            updated = deps.update_display_name(str(account["user_id"]), body.display_name.strip())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(deps.public_account(updated), headers=NO_STORE)

    @router.get("/me/branding")
    def get_branding(request: Request) -> JSONResponse:
        return JSONResponse(branding_payload(str(deps.current_account(request)["user_id"])), headers=NO_STORE)

    @router.put("/me/branding")
    def put_branding(request: Request, body: BrandingConfig) -> JSONResponse:
        user_id = str(deps.current_account(request)["user_id"])
        deps.store.save_config(user_id, body)
        return JSONResponse(branding_payload(user_id), headers=NO_STORE)

    @router.post("/me/branding/logo")
    async def upload_logo(request: Request, file: UploadFile = File(...)) -> JSONResponse:
        user_id = str(deps.current_account(request)["user_id"])
        data = await file.read(5 * 1024 * 1024 + 1)
        try:
            deps.store.save_logo(user_id, data)
        except BrandingError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(branding_payload(user_id), headers=NO_STORE)

    @router.delete("/me/branding/logo")
    def delete_logo(request: Request) -> JSONResponse:
        user_id = str(deps.current_account(request)["user_id"])
        deps.store.delete_logo(user_id)
        return JSONResponse(branding_payload(user_id), headers=NO_STORE)

    @router.get("/me/branding/logo")
    def get_logo(request: Request) -> FileResponse:
        path = deps.store.logo_path(str(deps.current_account(request)["user_id"]))
        if path is None:
            raise HTTPException(404, "Chưa có logo")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})

    @router.post("/me/branding/template")
    async def upload_template(request: Request, file: UploadFile = File(...)) -> JSONResponse:
        user_id = str(deps.current_account(request)["user_id"])
        data = await file.read(5 * 1024 * 1024 + 1)
        try:
            deps.store.save_template(user_id, data)
        except BrandingError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(branding_payload(user_id), headers=NO_STORE)

    @router.delete("/me/branding/template")
    def delete_template(request: Request) -> JSONResponse:
        user_id = str(deps.current_account(request)["user_id"])
        deps.store.delete_template(user_id)
        return JSONResponse(branding_payload(user_id), headers=NO_STORE)

    @router.get("/me/branding/template")
    def get_template(request: Request) -> FileResponse:
        path = deps.store.template_path(str(deps.current_account(request)["user_id"]))
        if path is None:
            raise HTTPException(404, "Chưa có logo mẫu")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})

    # ------------------------------------------------------------------ jobs
    @router.post("/branding/videos", status_code=202)
    async def queue_video(
        request: Request,
        file: UploadFile = File(...),
        # Replacing or covering another logo is only for videos the user may edit.
        rights_confirmed: bool = Form(False),
        override: PlacementOverride = Form("auto"),
    ) -> JSONResponse:
        account = deps.current_account(request)
        owner = str(account["user_id"])
        if not rights_confirmed:
            raise HTTPException(400, "Hãy xác nhận bạn có quyền chỉnh sửa video này")
        stored = deps.store.get(owner)
        logo_source = deps.store.logo_path(owner)
        if logo_source is None or stored["logo"] is None:
            raise HTTPException(400, "Hãy tải logo lên trong trang Tài khoản trước")
        extension = Path(file.filename or "video.mp4").suffix.lower() or ".mp4"
        if extension not in VIDEO_EXTENSIONS:
            raise HTTPException(400, "Định dạng video không hỗ trợ")
        job_id = uuid.uuid4().hex
        deps.reserve_quota(job_id, account)
        upload_dir = deps.upload_dir(owner, job_id)
        video_path = upload_dir / f"source{extension}"
        written = 0
        try:
            with video_path.open("wb") as buffer:
                while chunk := await file.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_VIDEO_BYTES:
                        raise HTTPException(413, "Video vượt quá 2048 MB")
                    buffer.write(chunk)
            try:
                deps.validate_video(video_path)
            except ValueError as exc:
                raise HTTPException(400, f"{file.filename or 'video'}: {exc}") from exc
            # Snapshot: later logo/setting changes never alter this job.
            logo_path = upload_dir / "logo.png"
            shutil.copyfile(logo_source, logo_path)
            template_source = deps.store.template_path(owner)
            template_path = upload_dir / "template.png" if template_source else None
            if template_source:
                shutil.copyfile(template_source, template_path)
        except BaseException:
            deps.release_quota(job_id)
            shutil.rmtree(upload_dir, ignore_errors=True)
            raise
        config = stored["config"]
        try:
            deps.create_job(job_id, {
                "status": "queued", "step": "queued", "progress": 0.0,
                "step_detail": "Video đã tải lên, đang chờ chèn logo",
                "filename": file.filename, "job_action": BRANDING_JOB_ACTION, "job_type": BRANDING_JOB_ACTION,
                "resume_request": {
                    "video_path": str(video_path),
                    "logo_path": str(logo_path), "logo_width": stored["logo"]["width"],
                    "logo_height": stored["logo"]["height"], "config": config.model_dump(mode="json"),
                    "override": override, "template_path": str(template_path) if template_path else None,
                },
            }, owner)
        except Exception:
            deps.release_quota(job_id)
            shutil.rmtree(upload_dir, ignore_errors=True)
            raise
        deps.notify_scheduler()
        return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)

    @router.get("/branding/videos")
    def history(
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        account = deps.current_account(request)
        # History is personal: even admins see only their own logo jobs here.
        jobs, total = deps.list_jobs(owner_user_id=str(account["user_id"]), job_action=BRANDING_JOB_ACTION,
                                     limit=limit, offset=offset)
        items = [deps.job_response(str(job["job_id"]), job) for job in jobs]
        return JSONResponse({"items": items, "total": total, "limit": limit, "offset": offset}, headers=NO_STORE)

    @router.get("/branding/videos/{job_id}")
    def video_status(request: Request, job_id: str) -> JSONResponse:
        return JSONResponse(deps.job_response(job_id, branding_job(request, job_id)), headers=NO_STORE)

    @router.get("/branding/videos/{job_id}/download")
    def download(request: Request, job_id: str) -> FileResponse:
        job = branding_job(request, job_id)
        filename = str(job.get("output_video") or "")
        target = deps.artifact_path(filename) if filename else None
        if target is None or not target.is_file():
            raise HTTPException(404, "Video chưa sẵn sàng")
        return FileResponse(target, filename=target.name, headers={"Cache-Control": "private, no-store"})

    @router.post("/branding/videos/{job_id}/reprocess", status_code=202)
    def reprocess(request: Request, job_id: str, body: ReprocessRequest) -> JSONResponse:
        """Re-render with a confirmed placement (after a "needs review" result) or another choice."""
        job = branding_job(request, job_id)
        if job.get("status") not in {"completed", "failed", "cancelled"}:
            raise HTTPException(409, "Job đang chạy")
        resume_request = dict(job.get("resume_request") or {})
        if not Path(str(resume_request.get("video_path", ""))).is_file():
            raise HTTPException(410, "Video nguồn đã bị xóa theo thời hạn lưu trữ")
        resume_request["override"] = body.override
        if body.cover_original is not None:
            resume_request["config"] = {**resume_request["config"], "cover_original": body.cover_original}
        deps.reserve_quota(job_id, deps.current_account(request))
        try:
            deps.retry_job(
                job_id, step="queued", progress=0.0, error=None, step_detail="Đang chờ chèn lại logo",
                started_at=None, finished_at=None, output_video=None, branding=None,
                resume_request=resume_request,
            )
        except Exception:
            deps.release_quota(job_id)
            raise
        deps.notify_scheduler()
        return JSONResponse({"job_id": job_id, "status": "queued", "override": body.override}, status_code=202)

    return router
