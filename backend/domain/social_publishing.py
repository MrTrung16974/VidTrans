from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


class SocialPlatform(StrEnum):
    TIKTOK = "TIKTOK"
    INSTAGRAM = "INSTAGRAM"
    THREADS = "THREADS"
    LINKEDIN = "LINKEDIN"
    PINTEREST = "PINTEREST"
    X = "X"


class PublishMode(StrEnum):
    DIRECT_NOW = "DIRECT_NOW"
    SCHEDULE_LOCAL = "SCHEDULE_LOCAL"
    DRAFT = "DRAFT"


class PublishStatus(StrEnum):
    READY = "READY"
    SCHEDULED_LOCAL = "SCHEDULED_LOCAL"
    CLAIMED = "CLAIMED"
    UPLOADING = "UPLOADING"
    PROCESSING_REMOTE = "PROCESSING_REMOTE"
    SUBMITTING = "SUBMITTING"
    PUBLISHED = "PUBLISHED"
    DRAFT_DELIVERED = "DRAFT_DELIVERED"
    FAILED = "FAILED"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = {
    PublishStatus.PUBLISHED.value,
    PublishStatus.DRAFT_DELIVERED.value,
    PublishStatus.FAILED.value,
    PublishStatus.CANCELLED.value,
}


@dataclass(frozen=True)
class PublisherCapabilities:
    supports_video: bool = True
    supports_text: bool = False
    supports_draft: bool = False
    supports_remote_status: bool = True
    supports_thumbnail: bool = False
    supports_first_comment: bool = False
    caption_limit: int = 2200
    schedule_horizon_days: int = 365


@dataclass(frozen=True)
class PublishRequest:
    video_path: Path
    caption: str
    mode: PublishMode = PublishMode.DIRECT_NOW
    account_id: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PublishResult:
    status: PublishStatus
    remote_media_id: str | None = None
    remote_post_id: str | None = None
    status_detail: Mapping[str, Any] = field(default_factory=dict)


RemoteCheckpoint = Callable[[str | None, str | None], None]
ProgressCallback = Callable[[int, int], None]


class SocialPublisher(Protocol):
    platform: SocialPlatform
    capabilities: PublisherCapabilities

    def publish(
        self,
        request: PublishRequest,
        *,
        on_checkpoint: RemoteCheckpoint,
        on_progress: ProgressCallback | None = None,
    ) -> PublishResult: ...

    def reconcile(
        self,
        *,
        remote_media_id: str | None,
        remote_post_id: str | None,
        account_id: str | None,
    ) -> PublishResult: ...

