from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from infrastructure.publishers.threads import ThreadsPublisher, ThreadsPublisherError


def create_threads_router(publisher: ThreadsPublisher, publisher_resolver=None,
                          oauth_session_registrar=None, callback_publisher_resolver=None) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["Threads Publishing"])

    @router.get("/threads-auth")
    def status(request: Request, account_id: str = Query(default="")) -> JSONResponse:
        active = publisher_resolver(request) if publisher_resolver else publisher
        result = active.connection_status(account_id or None)
        result["accounts"] = active.accounts()
        return JSONResponse(result)

    @router.get("/threads-auth/connect")
    def connect(request: Request) -> JSONResponse:
        try:
            active = publisher_resolver(request) if publisher_resolver else publisher
            url = active.authorization_url()
            if oauth_session_registrar: oauth_session_registrar(request, "threads", url)
            return JSONResponse({"authorization_url": url})
        except (ThreadsPublisherError, ValueError) as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.get("/threads-auth/callback")
    def callback(code: str = Query(default=""), state: str = Query(default=""),
                 error: str = Query(default=""), error_description: str = Query(default="")):
        try:
            if error:
                raise ThreadsPublisherError(error_description or error)
            active = callback_publisher_resolver("threads", state) if callback_publisher_resolver else publisher
            active.exchange_code(code, state)
            return RedirectResponse(url="/?threads=connected#tiktok", status_code=303)
        except ThreadsPublisherError as exc:
            return RedirectResponse(url=f"/?threads_error={quote_plus(str(exc))}#tiktok", status_code=303)

    @router.delete("/threads-auth")
    def disconnect(request: Request, account_id: str = Query(..., min_length=1, max_length=128)) -> JSONResponse:
        active = publisher_resolver(request) if publisher_resolver else publisher
        active.disconnect(account_id)
        result = active.connection_status()
        result["accounts"] = active.accounts()
        return JSONResponse(result)

    return router

