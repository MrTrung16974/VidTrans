from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from infrastructure.tiktok_publish_store import SQLiteTikTokPublishStore, utc_now
from infrastructure.tiktok_publisher import TikTokPublisher, TikTokPublisherError


logger = logging.getLogger(__name__)
MODES = {"DIRECT_NOW", "SCHEDULE", "INBOX_DRAFT"}


class TikTokPublishingValidationError(ValueError):
    pass


class TikTokPublishingService:
    def __init__(self, store: SQLiteTikTokPublishStore, publisher: TikTokPublisher) -> None:
        self.store = store
        self.publisher = publisher

    def create(
        self,
        *,
        job_id: str,
        video_path: Path,
        caption: str,
        mode: str,
        privacy_level: str,
        scheduled_for: str | None,
        timezone_name: str,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        if mode not in MODES:
            raise TikTokPublishingValidationError("Chế độ đăng TikTok không hợp lệ")
        if not video_path.is_file():
            raise TikTokPublishingValidationError("Không tìm thấy video đã dựng")
        caption = caption.strip()
        if mode != "INBOX_DRAFT" and not caption:
            raise TikTokPublishingValidationError("Caption không được để trống")
        if len(caption.encode("utf-16-le")) // 2 > 2200:
            raise TikTokPublishingValidationError("Caption vượt quá 2.200 ký tự")
        if not 8 <= len(idempotency_key) <= 128:
            raise TikTokPublishingValidationError("Idempotency key không hợp lệ")

        existing = self.store.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            if existing["job_id"] != job_id:
                raise TikTokPublishingValidationError(
                    "Idempotency key đã được dùng cho một video khác"
                )
            return existing, False
        latest = self.store.latest_for_job(job_id)
        active_statuses = {
            "READY", "SCHEDULED_LOCAL", "UPLOADING", "PROCESSING_UPLOAD",
            "PROCESSING_DOWNLOAD", "SUBMITTED", "NEEDS_RECONCILIATION",
        }
        if latest and latest["status"] in active_statuses:
            raise TikTokPublishingValidationError(
                "Video này đã có một yêu cầu đăng đang hoạt động. Hãy kiểm tra trạng thái trước khi tạo lại."
            )

        schedule_utc = None
        status = "READY"
        if mode == "SCHEDULE":
            if not scheduled_for:
                raise TikTokPublishingValidationError("Hãy chọn thời gian đăng")
            try:
                parsed = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
            except ValueError as exc:
                raise TikTokPublishingValidationError("Thời gian đăng không hợp lệ") from exc
            if parsed.tzinfo is None:
                raise TikTokPublishingValidationError("Thời gian đăng phải có timezone")
            parsed = parsed.astimezone(timezone.utc)
            now = datetime.now(timezone.utc)
            if parsed <= now + timedelta(seconds=30):
                raise TikTokPublishingValidationError("Lịch đăng phải cách hiện tại ít nhất 30 giây")
            if parsed > now + timedelta(days=365):
                raise TikTokPublishingValidationError("Lịch đăng không được quá 365 ngày")
            schedule_utc = parsed.isoformat()
            status = "SCHEDULED_LOCAL"

        payload = {
            "caption": caption,
            "video_path": str(video_path.resolve()),
            "video_size": video_path.stat().st_size,
            "privacy_level": privacy_level,
            "timezone": timezone_name or "UTC",
            "requested_at": utc_now(),
            "schedule_lag_seconds": None,
            "error": None,
        }
        attempt, created = self.store.create(
            idempotency_key=idempotency_key,
            job_id=job_id,
            mode=mode,
            status=status,
            scheduled_for=schedule_utc,
            payload=payload,
        )
        return attempt, created

    def run(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get(attempt_id)
        if attempt is None:
            raise TikTokPublishingValidationError("Không tìm thấy yêu cầu đăng TikTok")
        if attempt["status"] == "CANCELLED":
            return attempt
        if attempt["status"] == "SCHEDULED_LOCAL":
            raise TikTokPublishingValidationError("Yêu cầu chưa đến lịch đăng")
        if attempt["status"] == "READY":
            attempt = self.store.update(attempt_id, status="UPLOADING", error=None)

        remote_id = str(attempt.get("remote_publish_id") or "")
        if remote_id:
            return self.reconcile(attempt_id)

        def checkpoint(publish_id: str) -> None:
            self.store.update(attempt_id, remote_publish_id=publish_id, status="UPLOADING")

        try:
            path = Path(str(attempt["video_path"]))
            if attempt["mode"] == "INBOX_DRAFT":
                result = self.publisher.upload_draft(path, on_initialized=checkpoint)
            else:
                result = self.publisher.publish(
                    path,
                    str(attempt["caption"]),
                    privacy_level=str(attempt["privacy_level"]),
                    on_initialized=checkpoint,
                )
            status = str(result.get("status") or "SUBMITTED")
            return self.store.update(
                attempt_id,
                status=status,
                remote_publish_id=result["publish_id"],
                status_detail=result.get("status_payload"),
                error=None,
            )
        except Exception as exc:
            current = self.store.get(attempt_id) or attempt
            has_remote_id = bool(current.get("remote_publish_id"))
            logger.warning("TikTok publish attempt %s failed", attempt_id, exc_info=True)
            return self.store.update(
                attempt_id,
                status="NEEDS_RECONCILIATION" if has_remote_id else "FAILED",
                error=str(exc),
            )

    def reconcile(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get(attempt_id)
        if attempt is None:
            raise TikTokPublishingValidationError("Không tìm thấy yêu cầu đăng TikTok")
        publish_id = str(attempt.get("remote_publish_id") or "")
        if not publish_id:
            return attempt
        try:
            detail = self.publisher.fetch_status(publish_id)
        except TikTokPublisherError as exc:
            return self.store.update(attempt_id, status="NEEDS_RECONCILIATION", error=str(exc))
        status = str(detail.get("status") or "SUBMITTED")
        return self.store.update(
            attempt_id,
            status=status,
            status_detail=detail,
            error=detail.get("fail_reason") if status == "FAILED" else None,
        )


class TikTokPublishScheduler:
    def __init__(self, service: TikTokPublishingService, poll_interval: float = 1.0) -> None:
        self.service = service
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="tiktok-publish-scheduler")
        self.notify()

    async def stop(self) -> None:
        self._stopping = True
        self.notify()
        task, self._task = self._task, None
        if task is not None:
            await task

    def notify(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while not self._stopping:
            attempt = await asyncio.to_thread(self.service.store.claim_due)
            if attempt is not None:
                scheduled = datetime.fromisoformat(str(attempt["scheduled_for"]))
                lag = max(0.0, (datetime.now(timezone.utc) - scheduled).total_seconds())
                self.service.store.update(attempt["attempt_id"], schedule_lag_seconds=lag)
                await asyncio.to_thread(self.service.run, attempt["attempt_id"])
                continue
            pending = await asyncio.to_thread(self.service.store.next_reconcilable)
            if pending is not None:
                await asyncio.to_thread(self.service.reconcile, pending["attempt_id"])
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass
