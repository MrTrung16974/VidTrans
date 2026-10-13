from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse

from infrastructure.publishers.threads import ThreadsPublisher, ThreadsPublisherError


def create_threads_router(publisher: ThreadsPublisher) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["Threads Publishing"])

    @router.get("/threads-auth")
    def status(account_id: str = Query(default="")) -> JSONResponse:
        result = publisher.connection_status(account_id or None)
        result["accounts"] = publisher.accounts()
        return JSONResponse(result)

    @router.get("/threads-auth/connect")
    def connect() -> JSONResponse:
        try:
            return JSONResponse({"authorization_url": publisher.authorization_url()})
        except ThreadsPublisherError as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.get("/threads-auth/callback")
    def callback(code: str = Query(default=""), state: str = Query(default=""),
                 error: str = Query(default=""), error_description: str = Query(default="")):
        try:
            if error:
                raise ThreadsPublisherError(error_description or error)
            publisher.exchange_code(code, state)
            return RedirectResponse(url="/?threads=connected#tiktok", status_code=303)
        except ThreadsPublisherError as exc:
            return RedirectResponse(url=f"/?threads_error={quote_plus(str(exc))}#tiktok", status_code=303)

    @router.delete("/threads-auth")
    def disconnect(account_id: str = Query(..., min_length=1, max_length=128)) -> JSONResponse:
        publisher.disconnect(account_id)
        result = publisher.connection_status()
        result["accounts"] = publisher.accounts()
        return JSONResponse(result)

    return router

