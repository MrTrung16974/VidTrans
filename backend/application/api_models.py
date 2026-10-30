"""Response models of the job, batch, account and auth API, for the OpenAPI contract.

The routes return ``JSONResponse`` themselves, so these models never filter or
validate what is sent: they only describe it, letting the Flutter client be generated
with typed fields. Every model allows extra keys because job payloads grow with each
pipeline stage; clients must ignore keys they do not know.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class AuthStatus(_Open):
    enabled: bool
    configured: bool
    token_ttl_minutes: int | None = None
    refresh_token_ttl_days: int | None = None
    configuration_errors: list[str] = []
    message: str = ""
    authenticated: bool
    username: str | None = None
    role: str | None = None
    user_id: str | None = None


class AuthToken(_Open):
    access_token: str | None = Field(
        default=None, description="Missing only on a web refresh, which sets the cookie instead."
    )
    token_type: str = "bearer"
    expires_in: int
    scope: str = ""
    role: str
    remember: bool = False
    refresh_token: str | None = Field(default=None, description="Native login (client=native) only.")
    refresh_expires_in: int | None = Field(default=None, description="Native login (client=native) only.")


class Account(_Open):
    user_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    role: str | None = None
    approval_status: str | None = None
    plan: str | None = None
    payment_status: str | None = None
    approved_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    video_retention_days: int | None = None


class Job(_Open):
    """One job as GET /api/v1/jobs and the job events WebSocket serialize it."""

    job_id: str
    status: str | None = Field(
        default=None,
        description="queued, scheduled, processing, cancelling, completed, failed or cancelled.",
    )
    step: str | None = None
    step_detail: str | None = None
    progress: float | None = Field(default=None, description="0-100.")
    mode: int | None = None
    filename: str | None = None
    batch_id: str | None = None
    job_action: str | None = None
    job_type: str | None = None
    source_url: str | None = None
    source_platform: str | None = None
    subtitle_source: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    error: str | None = None
    queue_position: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    owner_user_id: str | None = None
    owner_email: str | None = None
    owner_display_name: str | None = None
    video_url: str | None = None
    subtitle_url: str | None = None
    translation_url: str | None = None
    tiktok_json_url: str | None = None
    tiktok_text_url: str | None = None
    download_all_url: str | None = None


class JobPage(_Open):
    items: list[Job]
    total: int
    limit: int
    offset: int
    page: int
    total_pages: int
    counts: dict[str, int] = Field(default_factory=dict, description="Job count per status for the caller.")


class CreatedBatchJob(_Open):
    job_id: str
    filename: str | None = None
    status: str
    source_url: str | None = None
    source_platform: str | None = None


class CreatedBatch(_Open):
    batch_id: str
    name: str
    total_jobs: int
    jobs: list[CreatedBatchJob]
    duplicate: bool | None = Field(
        default=None, description="True when an Idempotency-Key replay returned an earlier batch."
    )


class Batch(_Open):
    batch_id: str
    name: str
    status: str
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None
    counts: dict[str, int] | None = None
    total_jobs: int | None = None
    jobs: list[Job] | None = Field(default=None, description="GET /api/v1/batches/{batch_id} only.")


class BatchPage(_Open):
    items: list[Batch]
    total: int
    limit: int
    offset: int


class QueuedJob(_Open):
    job_id: str
    status: str
    duplicate: bool | None = Field(
        default=None, description="True when an Idempotency-Key replay returned an earlier job."
    )
