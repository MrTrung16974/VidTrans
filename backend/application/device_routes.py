"""Mobile push registration.

    /api/v1/me/devices         GET (list), POST (register or refresh an FCM token)
    /api/v1/me/devices/{id}    DELETE (sign-out on that phone)

Rows are scoped to the signed-in account. Viewers may register too: a push is
not a change to business data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from infrastructure.device_store import DeviceError, DeviceStore

NO_STORE = {"Cache-Control": "no-store"}


@dataclass
class DeviceRouteDependencies:
    store: DeviceStore
    current_account: Callable[[Request], dict[str, Any]]
    push_enabled: Callable[[], bool]


class DeviceRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=4096, description="FCM registration token")
    platform: Literal["android", "ios"]
    app_version: str | None = Field(default=None, max_length=64)


class Device(BaseModel):
    model_config = ConfigDict(extra="allow")
    device_id: str
    platform: str
    app_version: str | None = None
    created_at: str
    updated_at: str


class RegisteredDevice(Device):
    push_enabled: bool = Field(description="False when the server has no FCM credentials configured")


class DeviceList(BaseModel):
    items: list[Device]
    push_enabled: bool


def create_device_router(deps: DeviceRouteDependencies) -> APIRouter:
    router = APIRouter(prefix="/api/v1/me/devices", tags=["devices"])

    def user_id(request: Request) -> str:
        return str(deps.current_account(request)["user_id"])

    @router.get("", response_model=DeviceList)
    def list_devices(request: Request) -> JSONResponse:
        return JSONResponse(
            {"items": deps.store.list_for_user(user_id(request)), "push_enabled": deps.push_enabled()},
            headers=NO_STORE,
        )

    @router.post("", response_model=RegisteredDevice)
    def register_device(request: Request, body: DeviceRegistration) -> JSONResponse:
        try:
            device = deps.store.register(user_id(request), body.token, body.platform, body.app_version)
        except DeviceError as exc:
            raise HTTPException(422, str(exc)) from exc
        return JSONResponse({**device, "push_enabled": deps.push_enabled()}, headers=NO_STORE)

    @router.delete("/{device_id}", status_code=204)
    def delete_device(request: Request, device_id: str) -> None:
        if not deps.store.delete(user_id(request), device_id):
            raise HTTPException(404, "Không tìm thấy thiết bị")

    return router
