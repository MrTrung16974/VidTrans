"""HTTP API of account branding: the logo/settings page.

    /api/v1/me/branding            GET, PUT   settings of the signed-in account
    /api/v1/me/branding/logo       GET, POST, DELETE
    /api/v1/me/branding/template   GET, POST, DELETE  sample of the old logo to replace

The logo is stamped by translation and comic jobs that ask for it (``apply_logo``);
there is no separate "upload a video to brand" endpoint. Every lookup is scoped to
the caller's user id.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from application.api_models import Account
from domain.branding import LOGO_POSITIONS, BrandingConfig
from infrastructure.branding_store import BrandingError, BrandingStore

NO_STORE = {"Cache-Control": "no-store"}


@dataclass
class BrandingRouteDependencies:
    store: BrandingStore
    current_account: Callable[[Request], dict[str, Any]]
    update_display_name: Callable[[str, str], dict[str, Any]]
    public_account: Callable[[dict[str, Any]], dict[str, Any]]


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=120)


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

    # ------------------------------------------------------------------ account
    @router.put("/me", response_model=Account)
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

    return router
