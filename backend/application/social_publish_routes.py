from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from application.social_publishing import (
    SocialPublishingService,
    SocialPublishingValidationError,
)
from domain.social_publishing import PublishMode
from infrastructure.publishers.registry import PublisherNotConfiguredError
from infrastructure.social_publish_store import (
    SocialPublishNotFoundError,
    SocialPublishStoreError,
)


class CreateSocialPublishRequest(BaseModel):
    platform: str
    connection_id: str | None = Field(default=None, max_length=64)
    # Temporary compatibility path for providers not migrated to encrypted connections yet.
    account_id: str | None = Field(default=None, max_length=128)
    mode: str
    caption: str = Field(default="", max_length=10_000)
    scheduled_for: str | None = None
    timezone: str = Field(default="UTC", max_length=64)
    options: dict[str, Any] = Field(default_factory=dict)
    reviewed: bool
    idempotency_key: str = Field(min_length=8, max_length=128)


def create_social_publish_router(
    service: SocialPublishingService,
    jobs: Any,
    output_dir: Path,
    owner_resolver=None,
    connection_resolver=None,
    can_manage_all_resolver=None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["Social Publishing"])
    output_dir = output_dir.resolve()
    owner_resolver = owner_resolver or (lambda _request: "__legacy__")
    can_manage_all_resolver = can_manage_all_resolver or (lambda _request: False)

    def owned_job(request: Request, job_id: str):
        if can_manage_all_resolver(request):
            return jobs.get(job_id)
        return jobs.get_for_user(job_id, owner_resolver(request)) if hasattr(jobs, "get_for_user") else jobs.get(job_id)

    def public_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "attempt_id", "job_id", "platform", "connection_id", "account_id", "mode", "status",
            "scheduled_for", "remote_media_id", "remote_post_id", "status_detail",
            "error_code", "error_message", "created_at", "updated_at", "timezone",
        }
        return {key: value for key, value in attempt.items() if key in allowed}

    def ready_video(request: Request, job_id: str) -> Path:
        job = owned_job(request, job_id)
        if not job:
            raise HTTPException(404, "Không tìm thấy video")
        if job.get("status") != "completed" or not job.get("output_video"):
            raise HTTPException(409, "Video phải xử lý hoàn tất trước khi đăng")
        video = (output_dir / str(job["output_video"])).resolve()
        if not video.is_relative_to(output_dir) or not video.is_file():
            raise HTTPException(404, "Không tìm thấy file đầu ra")
        return video

    @router.get("/social-platforms")
    def platforms():
        return {"items": service.publishers.capabilities()}

    @router.post("/jobs/{job_id}/social-publishes", status_code=202)
    def create_publish(http_request: Request, job_id: str, request: CreateSocialPublishRequest):
        if not request.reviewed:
            raise HTTPException(422, "Hãy duyệt video và nội dung trước khi đăng")
        video = ready_video(http_request, job_id)
        owner_user_id = str(owner_resolver(http_request))
        account_id = request.account_id
        connection_id = request.connection_id
        if connection_id:
            if connection_resolver is None:
                raise HTTPException(503, "Hệ thống connection chưa sẵn sàng")
            try:
                connection = connection_resolver(
                    owner_user_id, connection_id, request.platform
                )
            except (KeyError, ValueError) as exc:
                raise HTTPException(404, "Không tìm thấy kết nối mạng xã hội") from exc
            account_id = str(connection["provider_account_id"])
        elif request.platform.upper() in {"YOUTUBE", "FACEBOOK", "INSTAGRAM"}:
            raise HTTPException(422, f"Hãy chọn kết nối {request.platform.title()}")
        try:
            attempt, created = service.create(
                owner_user_id=owner_user_id,
                connection_id=connection_id,
                job_id=job_id,
                platform=request.platform,
                video_path=video,
                caption=request.caption,
                mode=request.mode,
                scheduled_for=request.scheduled_for,
                timezone_name=request.timezone,
                idempotency_key=hashlib.sha256(
                    f"{owner_user_id}:{request.idempotency_key}".encode("utf-8")
                ).hexdigest(),
                account_id=account_id,
                options=request.options,
            )
        except (SocialPublishingValidationError, PublisherNotConfiguredError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except SocialPublishStoreError as exc:
            raise HTTPException(409, str(exc)) from exc
        if created and request.mode != PublishMode.SCHEDULE_LOCAL.value:
            threading.Thread(
                target=service.run,
                args=(str(attempt["attempt_id"]),),
                daemon=True,
                name=f"social-publish-{str(attempt['attempt_id'])[:8]}",
            ).start()
        return {**public_attempt(attempt), "created": created}

    @router.get("/jobs/{job_id}/social-publishes")
    def list_publishes(request: Request, job_id: str):
        owner_user_id = str(owner_resolver(request))
        if owned_job(request, job_id) is None:
            raise HTTPException(404, "Không tìm thấy video")
        return {"items": [
            public_attempt(item)
            for item in service.store.list_for_job(job_id, owner_user_id=owner_user_id)
        ]}

    @router.get("/social-publishes/{attempt_id}")
    def get_publish(request: Request, attempt_id: str):
        attempt = service.store.get(attempt_id, owner_user_id=str(owner_resolver(request)))
        if attempt is None or owned_job(request, str(attempt["job_id"])) is None:
            raise HTTPException(404, "Không tìm thấy yêu cầu đăng")
        return public_attempt(attempt)

    @router.post("/social-publishes/{attempt_id}/cancel")
    def cancel_publish(request: Request, attempt_id: str):
        attempt = service.store.get(attempt_id, owner_user_id=str(owner_resolver(request)))
        if attempt is None or owned_job(request, str(attempt["job_id"])) is None:
            raise HTTPException(404, "Không tìm thấy yêu cầu đăng")
        try:
            return public_attempt(service.store.cancel(attempt_id))
        except SocialPublishNotFoundError as exc:
            raise HTTPException(404, "Không tìm thấy yêu cầu đăng") from exc
        except SocialPublishStoreError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/social-publishes/{attempt_id}/refresh")
    def refresh_publish(request: Request, attempt_id: str):
        attempt = service.store.get(attempt_id, owner_user_id=str(owner_resolver(request)))
        if attempt is None or owned_job(request, str(attempt["job_id"])) is None:
            raise HTTPException(404, "Không tìm thấy yêu cầu đăng")
        if attempt.get("remote_media_id") or attempt.get("remote_post_id"):
            attempt = service.reconcile(attempt_id)
        return public_attempt(attempt)

    return router

