from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from infrastructure.social_credentials import SocialCredentialError, SocialCredentialStore


def create_social_connection_router(
    store: SocialCredentialStore,
    *,
    owner_resolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/social-connections", tags=["Social Connections"])

    @router.get("")
    def list_connections(request: Request, platform: str | None = None) -> JSONResponse:
        user_id = str(owner_resolver(request))
        try:
            items = store.list_for_user(user_id=user_id, platform=platform)
        except SocialCredentialError as exc:
            raise HTTPException(422, str(exc)) from exc
        public_items = [
            {
                key: item.get(key)
                for key in (
                    "connection_id", "platform", "provider_account_id", "display_name",
                    "status", "scopes", "expires_at", "refresh_expires_at",
                    "created_at", "updated_at",
                )
            }
            for item in items
        ]
        return JSONResponse({"items": public_items})

    @router.get("/{connection_id}")
    def get_connection(request: Request, connection_id: str) -> JSONResponse:
        try:
            item = store.get_for_user(
                user_id=str(owner_resolver(request)), connection_id=connection_id
            )
        except SocialCredentialError as exc:
            raise HTTPException(404, "Không tìm thấy kết nối mạng xã hội") from exc
        return JSONResponse({
            key: item.get(key)
            for key in (
                "connection_id", "platform", "provider_account_id", "display_name",
                "status", "scopes", "expires_at", "refresh_expires_at",
                "created_at", "updated_at",
            )
        })

    @router.delete("/{connection_id}")
    def disconnect(request: Request, connection_id: str) -> JSONResponse:
        try:
            item = store.revoke(
                user_id=str(owner_resolver(request)), connection_id=connection_id
            )
        except SocialCredentialError as exc:
            raise HTTPException(404, "Không tìm thấy kết nối mạng xã hội") from exc
        return JSONResponse({
            "connection_id": item["connection_id"],
            "platform": item["platform"],
            "status": item["status"],
        })

    return router

