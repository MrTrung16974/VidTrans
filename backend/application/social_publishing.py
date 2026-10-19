from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from domain.social_publishing import (
    PublishMode,
    PublishRequest,
    PublishResult,
    PublishStatus,
    SocialPlatform,
)
from infrastructure.publishers.registry import PublisherRegistry
from infrastructure.social_publish_store import SocialPublishStore, utc_now


logger = logging.getLogger(__name__)


class SocialPublishingValidationError(ValueError):
    pass


class SocialPublishingService:
    def __init__(
        self,
        store: SocialPublishStore,
        publishers: PublisherRegistry,
        publisher_resolver=None,
    ) -> None:
        self.store = store
        self.publishers = publishers
        self._publisher_resolver = publisher_resolver

    def _publisher(self, platform: SocialPlatform | str, job_id: str):
        if self._publisher_resolver is not None:
            return self._publisher_resolver(platform, job_id)
        return self.publishers.get(platform)

    def create(
        self,
        *,
        owner_user_id: str = "__legacy__",
        connection_id: str | None = None,
        job_id: str,
        platform: str,
        video_path: Path,
        caption: str,
        mode: str,
        scheduled_for: str | None,
        timezone_name: str,
        idempotency_key: str,
        account_id: str | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        try:
            normalized_platform = SocialPlatform(platform)
            normalized_mode = PublishMode(mode)
        except ValueError as exc:
            raise SocialPublishingValidationError("Nền tảng hoặc chế độ đăng không hợp lệ") from exc
        publisher = self._publisher(normalized_platform, job_id)
        capabilities = publisher.capabilities
        video_path = video_path.resolve()
        if not capabilities.supports_video:
            raise SocialPublishingValidationError("Nền tảng này không hỗ trợ đăng video")
        if not video_path.is_file():
            raise SocialPublishingValidationError("Không tìm thấy video đã dựng")
        caption = caption.strip()
        if normalized_mode is not PublishMode.DRAFT and not caption:
            raise SocialPublishingValidationError("Caption không được để trống")
        if len(caption) > capabilities.caption_limit:
            raise SocialPublishingValidationError(
                f"Caption vượt quá giới hạn {capabilities.caption_limit} ký tự"
            )
        if normalized_mode is PublishMode.DRAFT and not capabilities.supports_draft:
            raise SocialPublishingValidationError("Nền tảng này không hỗ trợ bản nháp")
        if not 8 <= len(idempotency_key) <= 128:
            raise SocialPublishingValidationError("Idempotency key không hợp lệ")

        schedule_utc = None
        status = PublishStatus.READY
        if normalized_mode is PublishMode.SCHEDULE_LOCAL:
            if not scheduled_for:
                raise SocialPublishingValidationError("Hãy chọn thời gian đăng")
            try:
                parsed = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
            except ValueError as exc:
                raise SocialPublishingValidationError("Thời gian đăng không hợp lệ") from exc
            if parsed.tzinfo is None:
                raise SocialPublishingValidationError("Thời gian đăng phải có timezone")
            parsed = parsed.astimezone(timezone.utc)
            now = datetime.now(timezone.utc)
            if parsed <= now + timedelta(seconds=30):
                raise SocialPublishingValidationError(
                    "Lịch đăng phải cách hiện tại ít nhất 30 giây"
                )
            if parsed > now + timedelta(days=capabilities.schedule_horizon_days):
                raise SocialPublishingValidationError("Lịch đăng vượt quá giới hạn nền tảng")
            schedule_utc = parsed.isoformat()
            status = PublishStatus.SCHEDULED_LOCAL

        payload = {
            "caption": caption,
            "video_path": str(video_path),
            "video_size": video_path.stat().st_size,
            "timezone": timezone_name or "UTC",
            "options": dict(options or {}),
            "requested_at": utc_now(),
        }
        return self.store.create(
            owner_user_id=owner_user_id,
            connection_id=connection_id,
            job_id=job_id,
            platform=normalized_platform.value,
            account_id=account_id,
            mode=normalized_mode.value,
            status=status.value,
            scheduled_for=schedule_utc,
            idempotency_key=idempotency_key,
            payload=payload,
        )

    def run(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get(attempt_id)
        if attempt is None:
            raise SocialPublishingValidationError("Không tìm thấy yêu cầu đăng")
        if attempt["status"] == PublishStatus.CANCELLED.value:
            return attempt
        if attempt["status"] == PublishStatus.SCHEDULED_LOCAL.value:
            raise SocialPublishingValidationError("Yêu cầu chưa đến lịch đăng")
        if attempt.get("remote_media_id") or attempt.get("remote_post_id"):
            return self.reconcile(attempt_id)

        publisher = self._publisher(str(attempt["platform"]), str(attempt["job_id"]))
        self.store.update(
            attempt_id,
            status=PublishStatus.UPLOADING.value,
            error_code=None,
            error_message=None,
        )

        def checkpoint(media_id: str | None, post_id: str | None) -> None:
            updates: dict[str, Any] = {"status": PublishStatus.UPLOADING.value}
            if media_id:
                updates["remote_media_id"] = media_id
            if post_id:
                updates["remote_post_id"] = post_id
            self.store.update(attempt_id, **updates)

        last_progress = -1

        def progress(uploaded: int, total: int) -> None:
            nonlocal last_progress
            percent = min(100, int(uploaded * 100 / total)) if total else 0
            if percent == last_progress:
                return
            last_progress = percent
            self.store.update(
                attempt_id,
                status=PublishStatus.UPLOADING.value,
                status_detail={
                    "uploaded_bytes": uploaded,
                    "total_bytes": total,
                    "percent": percent,
                },
            )

        try:
            result = publisher.publish(
                PublishRequest(
                    video_path=Path(str(attempt["video_path"])),
                    caption=str(attempt["caption"]),
                    mode=PublishMode(str(attempt["mode"])),
                    account_id=attempt.get("account_id"),
                    options=attempt.get("options") or {},
                ),
                on_checkpoint=checkpoint,
                on_progress=progress,
            )
            return self._save_result(attempt_id, result)
        except Exception as exc:
            current = self.store.get(attempt_id) or attempt
            has_remote_side_effect = bool(
                current.get("remote_media_id") or current.get("remote_post_id")
            )
            logger.warning("Social publish attempt %s failed", attempt_id, exc_info=True)
            return self.store.update(
                attempt_id,
                status=(
                    PublishStatus.NEEDS_RECONCILIATION.value
                    if has_remote_side_effect
                    else PublishStatus.FAILED.value
                ),
                error_code=type(exc).__name__,
                error_message=str(exc)[:500],
            )

    def reconcile(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get(attempt_id)
        if attempt is None:
            raise SocialPublishingValidationError("Không tìm thấy yêu cầu đăng")
        if not attempt.get("remote_media_id") and not attempt.get("remote_post_id"):
            return attempt
        publisher = self._publisher(str(attempt["platform"]), str(attempt["job_id"]))
        try:
            result = publisher.reconcile(
                remote_media_id=attempt.get("remote_media_id"),
                remote_post_id=attempt.get("remote_post_id"),
                account_id=attempt.get("account_id"),
            )
            return self._save_result(attempt_id, result)
        except Exception as exc:
            logger.warning("Unable to reconcile social publish %s", attempt_id, exc_info=True)
            return self.store.update(
                attempt_id,
                status=PublishStatus.NEEDS_RECONCILIATION.value,
                error_code=type(exc).__name__,
                error_message=str(exc)[:500],
            )

    def _save_result(self, attempt_id: str, result: PublishResult) -> dict[str, Any]:
        updates: dict[str, Any] = {
            "status": result.status.value,
            "status_detail": dict(result.status_detail),
            "error_code": None,
            "error_message": None,
        }
        if result.remote_media_id:
            updates["remote_media_id"] = result.remote_media_id
        if result.remote_post_id:
            updates["remote_post_id"] = result.remote_post_id
        return self.store.update(attempt_id, **updates)


class SocialPublishScheduler:
    def __init__(self, service: SocialPublishingService, poll_interval: float = 1.0) -> None:
        self.service = service
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="social-publish-scheduler")
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
                await asyncio.to_thread(self.service.run, str(attempt["attempt_id"]))
                continue
            pending = await asyncio.to_thread(self.service.store.next_reconcilable)
            if pending is not None:
                await asyncio.to_thread(self.service.reconcile, str(pending["attempt_id"]))
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass

