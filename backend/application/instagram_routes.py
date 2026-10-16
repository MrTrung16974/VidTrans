from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from infrastructure.publishers.instagram import InstagramPublisher, InstagramPublisherError
from infrastructure.signed_media import SignedMediaError, SignedMediaUrlService


def create_instagram_router(
    publisher: InstagramPublisher,
    media_urls: SignedMediaUrlService,
    publisher_resolver=None,
    oauth_session_registrar=None,
    callback_publisher_resolver=None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["Instagram Publishing"])

    @router.get("/instagram-auth")
    def status(request: Request, account_id: str = Query(default="")) -> JSONResponse:
        active = publisher_resolver(request) if publisher_resolver else publisher
        result = active.connection_status(account_id or None)
        result["accounts"] = active.accounts()
        return JSONResponse(result)

    @router.get("/instagram-auth/connect")
    def connect(request: Request) -> JSONResponse:
        try:
            active = publisher_resolver(request) if publisher_resolver else publisher
            url = active.authorization_url()
            if oauth_session_registrar:
                oauth_session_registrar(request, "instagram", url)
            return JSONResponse({"authorization_url": url})
        except (InstagramPublisherError, ValueError) as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.get("/instagram-auth/callback")
    def callback(
        code: str = Query(default=""),
        state: str = Query(default=""),
        error: str = Query(default=""),
        error_description: str = Query(default=""),
    ) -> RedirectResponse:
        try:
            if error:
                raise InstagramPublisherError(error_description or error)
            active = callback_publisher_resolver("instagram", state) if callback_publisher_resolver else publisher
            active.exchange_code(code, state)
            return RedirectResponse(url="/?instagram=connected#tiktok", status_code=303)
        except InstagramPublisherError as exc:
            message = quote_plus(str(exc))
            return RedirectResponse(
                url=f"/?instagram_error={message}#tiktok", status_code=303
            )

    @router.delete("/instagram-auth")
    def disconnect(request: Request, account_id: str = Query(..., min_length=1, max_length=128)) -> JSONResponse:
        active = publisher_resolver(request) if publisher_resolver else publisher
        active.disconnect(account_id)
        result = active.connection_status(account_id)
        result["accounts"] = active.accounts()
        return JSONResponse(result)

    @router.get("/social-media/{token}", include_in_schema=False)
    def signed_media(token: str) -> FileResponse:
        try:
            path = media_urls.resolve(token)
        except SignedMediaError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(
            path,
            media_type="video/mp4",
            filename=path.name,
            headers={"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"},
        )

    return router

