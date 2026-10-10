from __future__ import annotations

import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException

from application.tiktok_publishing import (
    TikTokPublishingService,
    TikTokPublishingValidationError,
)
from infrastructure.tiktok_publish_store import (
    TikTokPublishNotFoundError,
    TikTokPublishStoreError,
)


def create_tiktok_publish_router(service: TikTokPublishingService, jobs, output_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["TikTok Publishing"])
    output_dir = output_dir.resolve()

    def public_attempt(attempt):
        allowed = {
            "attempt_id", "job_id", "mode", "status", "scheduled_for",
            "remote_publish_id", "created_at", "updated_at", "timezone",
            "privacy_level", "error", "status_detail", "schedule_lag_seconds",
        }
        return {key: value for key, value in attempt.items() if key in allowed}

    def ready_job(job_id: str):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Không tìm thấy video")
        if job.get("status") != "completed" or not job.get("output_video"):
            raise HTTPException(409, "Video phải xử lý hoàn tất trước khi đăng")
        video = (output_dir / str(job["output_video"])).resolve()
        if video.parent != output_dir or not video.is_file():
            raise HTTPException(404, "Không tìm thấy file đầu ra")
        return job, video

    @router.post("/jobs/{job_id}/tiktok-publishes", status_code=202)
    def create_publish(
        job_id: str,
        caption: str = Form("", max_length=2200),
        mode: str = Form(...),
        privacy_level: str = Form("SELF_ONLY"),
        scheduled_for: str | None = Form(None),
        timezone_name: str = Form("UTC", max_length=64),
        idempotency_key: str = Form(..., min_length=8, max_length=128),
        reviewed: bool = Form(...),
    ):
        if not reviewed:
            raise HTTPException(422, "Hãy duyệt video, caption và tài khoản trước khi đăng")
        _, video = ready_job(job_id)
        try:
            attempt, created = service.create(
                job_id=job_id,
                video_path=video,
                caption=caption,
                mode=mode,
                privacy_level=privacy_level,
                scheduled_for=scheduled_for,
                timezone_name=timezone_name,
                idempotency_key=idempotency_key,
            )
        except TikTokPublishingValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        except TikTokPublishStoreError as exc:
            raise HTTPException(409, str(exc)) from exc
        if created and mode != "SCHEDULE":
            threading.Thread(
                target=service.run,
                args=(attempt["attempt_id"],),
                daemon=True,
                name=f"tiktok-publish-{attempt['attempt_id'][:8]}",
            ).start()
        return {**public_attempt(attempt), "created": created}

    @router.get("/tiktok-publishes/{attempt_id}")
    def get_publish(attempt_id: str, refresh: bool = False):
        attempt = service.store.get(attempt_id)
        if attempt is None:
            raise HTTPException(404, "Không tìm thấy yêu cầu đăng TikTok")
        if refresh and attempt.get("remote_publish_id"):
            attempt = service.reconcile(attempt_id)
        return public_attempt(attempt)

    @router.get("/jobs/{job_id}/tiktok-publishes/latest")
    def latest_publish(job_id: str):
        ready_job(job_id)
        attempt = service.store.latest_for_job(job_id)
        return public_attempt(attempt) if attempt else {"job_id": job_id, "status": None}

    @router.post("/tiktok-publishes/{attempt_id}/cancel")
    def cancel_publish(attempt_id: str):
        try:
            return public_attempt(service.store.cancel(attempt_id))
        except TikTokPublishNotFoundError as exc:
            raise HTTPException(404, "Không tìm thấy yêu cầu đăng TikTok") from exc
        except TikTokPublishStoreError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/jobs/{job_id}/tiktok-publish-video")
    def publish_video(job_id: str):
        _, video = ready_job(job_id)
        return {"video_url": f"/download/{quote(video.name)}"}

    return router
