"""Background-music library: admins upload, every signed-in user picks one for dubbing.

    GET  /api/v1/music-tracks                         signed-in users
    GET  /api/v1/music-tracks/{id}/audio              signed-in users (preview)
    POST /api/v1/admin/music-tracks                   admin, multipart title + file, audited
    POST /api/v1/admin/music-tracks/{id}              admin, rename, audited
    DELETE /api/v1/admin/music-tracks/{id}            admin, audited
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from infrastructure.music_library import (
    MAX_MUSIC_BYTES,
    MEDIA_TYPES,
    MusicLibraryError,
    MusicLibraryStore,
    MusicTrackNotFound,
)

NO_STORE = {"Cache-Control": "no-store"}


@dataclass
class MusicRouteDependencies:
    store: MusicLibraryStore
    current_account: Callable[[Request], dict[str, Any]]
    require_admin: Callable[[Request], dict[str, Any]]
    audit: Callable[..., Any]


def public_track(track: dict[str, Any]) -> dict[str, Any]:
    return {
        "track_id": track["track_id"],
        "title": track["title"],
        "duration_seconds": track["duration_seconds"],
        "size_bytes": track["size_bytes"],
        "extension": track["extension"],
        "created_at": track["created_at"],
        "audio_url": f"/api/v1/music-tracks/{track['track_id']}/audio",
    }


def create_music_router(deps: MusicRouteDependencies) -> APIRouter:
    router = APIRouter(tags=["music"])

    def lookup(track_id: str) -> dict[str, Any]:
        try:
            return deps.store.get(track_id)
        except MusicTrackNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/v1/music-tracks")
    def list_music_tracks(request: Request) -> JSONResponse:
        deps.current_account(request)
        return JSONResponse({"items": [public_track(t) for t in deps.store.list()]}, headers=NO_STORE)

    @router.get("/api/v1/music-tracks/{track_id}/audio")
    def music_track_audio(request: Request, track_id: str) -> FileResponse:
        deps.current_account(request)
        track = lookup(track_id)
        path = deps.store.file_path(track)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="File nhạc không còn trên máy chủ")
        return FileResponse(
            path,
            media_type=MEDIA_TYPES.get(track["extension"], "application/octet-stream"),
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @router.post("/api/v1/admin/music-tracks", status_code=201)
    async def upload_music_track(
        request: Request,
        file: UploadFile = File(...),
        title: str = Form("", max_length=200),
    ) -> JSONResponse:
        actor = deps.require_admin(request)
        data = await file.read(MAX_MUSIC_BYTES + 1)
        try:
            track = deps.store.create(
                title=title, file_name=file.filename or "", data=data, created_by=str(actor["user_id"]),
            )
        except MusicLibraryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        deps.audit(
            actor_user_id=actor["user_id"],
            action="admin.music_track.create",
            resource_type="music_track",
            resource_id=track["track_id"],
            metadata={"title": track["title"]},
        )
        return JSONResponse(public_track(track), status_code=201, headers=NO_STORE)

    @router.post("/api/v1/admin/music-tracks/{track_id}")
    def rename_music_track(request: Request, track_id: str, title: str = Form(..., max_length=200)) -> JSONResponse:
        actor = deps.require_admin(request)
        lookup(track_id)
        try:
            track = deps.store.rename(track_id, title)
        except MusicLibraryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        deps.audit(
            actor_user_id=actor["user_id"],
            action="admin.music_track.rename",
            resource_type="music_track",
            resource_id=track_id,
            metadata={"title": track["title"]},
        )
        return JSONResponse(public_track(track), headers=NO_STORE)

    @router.delete("/api/v1/admin/music-tracks/{track_id}")
    def delete_music_track(request: Request, track_id: str) -> JSONResponse:
        actor = deps.require_admin(request)
        lookup(track_id)
        track = deps.store.delete(track_id)
        deps.audit(
            actor_user_id=actor["user_id"],
            action="admin.music_track.delete",
            resource_type="music_track",
            resource_id=track_id,
            metadata={"title": track["title"]},
        )
        return JSONResponse({"deleted": track_id}, headers=NO_STORE)

    return router
