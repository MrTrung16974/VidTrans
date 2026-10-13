from __future__ import annotations

from typing import Any, Mapping

from domain.social_publishing import (
    PublishMode,
    PublishResult,
    PublishStatus,
    PublisherCapabilities,
    SocialPlatform,
)
from infrastructure.tiktok_publisher import TikTokPublisher


class TikTokSocialPublisher:
    """Contract adapter that preserves the proven TikTok upload implementation."""

    platform = SocialPlatform.TIKTOK
    capabilities = PublisherCapabilities(
        supports_video=True,
        supports_text=False,
        supports_draft=True,
        supports_remote_status=True,
        caption_limit=2200,
        schedule_horizon_days=365,
    )

    def __init__(self, publisher: TikTokPublisher) -> None:
        self.publisher = publisher

    def publish(self, request, *, on_checkpoint, on_progress=None):
        def checkpoint(publish_id: str) -> None:
            on_checkpoint(publish_id, None)

        if request.mode is PublishMode.DRAFT:
            result = self.publisher.upload_draft(
                request.video_path,
                on_initialized=checkpoint,
                account_id=request.account_id,
                on_progress=on_progress,
            )
        else:
            options = dict(request.options)
            result = self.publisher.publish(
                request.video_path,
                request.caption,
                privacy_level=str(options.get("privacy_level") or "SELF_ONLY"),
                disable_comment=bool(options.get("disable_comment", False)),
                disable_duet=bool(options.get("disable_duet", False)),
                disable_stitch=bool(options.get("disable_stitch", False)),
                on_initialized=checkpoint,
                account_id=request.account_id,
                on_progress=on_progress,
            )
        publish_id = str(result.get("publish_id") or "") or None
        return PublishResult(
            status=self.map_status(str(result.get("status") or "SUBMITTED")),
            remote_media_id=publish_id,
            status_detail=self._safe_detail(result.get("status_payload")),
        )

    def reconcile(self, *, remote_media_id, remote_post_id, account_id):
        publish_id = remote_post_id or remote_media_id
        if not publish_id:
            raise ValueError("Thiếu TikTok publish ID")
        detail = self.publisher.fetch_status(str(publish_id), account_id=account_id)
        return PublishResult(
            status=self.map_status(str(detail.get("status") or "SUBMITTED")),
            remote_media_id=str(publish_id),
            status_detail=self._safe_detail(detail),
        )

    @staticmethod
    def map_status(status: str) -> PublishStatus:
        normalized = status.upper()
        if normalized == "PUBLISH_COMPLETE":
            return PublishStatus.PUBLISHED
        if normalized == "SEND_TO_USER_INBOX":
            return PublishStatus.DRAFT_DELIVERED
        if normalized == "FAILED":
            return PublishStatus.FAILED
        if normalized in {
            "PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD", "SUBMITTED", "UPLOADING",
        }:
            return PublishStatus.PROCESSING_REMOTE
        return PublishStatus.NEEDS_RECONCILIATION

    @staticmethod
    def _safe_detail(value: Any) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed = {
            "status", "fail_reason", "uploaded_bytes", "total_bytes", "percent",
            "publicaly_available_post_id",
        }
        return {key: item for key, item in value.items() if key in allowed}

