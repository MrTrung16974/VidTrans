"""HTTP API for importing novel chapters into the story → comic form."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from infrastructure.novel_sources import NovelImportService, NovelSourceError

IMPORT_ROLES = ("owner", "admin", "editor")


class NovelImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    chapter_ids: list[str] = Field(min_length=1, max_length=200)
    # Explicit consent: the person importing has the right to adapt this text.
    rights_confirmed: bool


def create_novel_import_router(
    service: NovelImportService,
    require_roles: Callable[..., dict[str, Any]],
    ensure_allowed: Callable[[dict[str, Any]], None] = lambda account: None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/comic/sources", tags=["Comic novel import"])

    def account(request: Request) -> dict[str, Any]:
        user = require_roles(request, *IMPORT_ROLES)
        # Every call is gated, reads included: a catalog lookup opens the source site.
        ensure_allowed(user)
        return user

    def call(operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except NovelSourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def owned(request: Request, import_id: str, **options: Any) -> dict[str, Any]:
        try:
            return service.status(str(account(request)["user_id"]), import_id, **options)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Không tìm thấy lượt tải truyện") from exc

    @router.get("")
    def list_sources(request: Request) -> dict[str, Any]:
        account(request)
        return {"sources": service.supported_sources(), "episode_chars": service.episode_chars}

    @router.get("/catalog")
    def get_catalog(
        request: Request,
        url: str = Query(min_length=1, max_length=500),
        refresh: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Book title, author and chapters, with each chapter's access and cache state."""
        account(request)
        catalog = call(lambda: service.catalog(url, refresh=refresh))
        cached = service.cached_chapter_ids(catalog)
        payload = catalog.as_dict()
        payload["chapters"] = [{**chapter, "cached": chapter["id"] in cached} for chapter in payload["chapters"]]
        payload["episode_chars"] = service.episode_chars
        return payload

    @router.post("/imports", status_code=202)
    def start_import(request: Request, body: NovelImportRequest) -> dict[str, Any]:
        user = account(request)
        if not body.rights_confirmed:
            raise HTTPException(status_code=422, detail="Hãy xác nhận bạn là tác giả hoặc đã được phép chuyển thể truyện này")
        return call(lambda: service.start_import(str(user["user_id"]), body.url, body.chapter_ids))

    @router.get("/imports/{import_id}")
    def get_import(
        request: Request, import_id: str, include_text: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Progress per chapter; ``include_text`` adds each episode's text once it is done."""
        return owned(request, import_id, include_text=include_text)

    @router.delete("/imports/{import_id}")
    def cancel_import(request: Request, import_id: str) -> dict[str, Any]:
        user = account(request)
        try:
            return service.cancel(str(user["user_id"]), import_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Không tìm thấy lượt tải truyện") from exc

    return router
