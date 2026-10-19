from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sqlite3

from infrastructure.database import connect, prepare_target
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


logger = logging.getLogger(__name__)

_PUBLIC_JOB_FIELDS = {
    "batch_id",
    "status",
    "step",
    "progress",
    "source_platform",
    "source_language",
    "target_language",
    "review_cues",
    "translation_fallback_cues",
    "tiktok_publish_status",
}

_STEP_EVENTS = {
    "downloading-source": "download.started",
    "translating": "translation.started",
    "translation-ready": "translation.completed",
    "publishing-tiktok": "publish.started",
}

_STATUS_EVENTS = {
    "completed": "job.completed",
    "failed": "job.failed",
    "cancelled": "job.cancelled",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_bounded_int(value: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


@dataclass(frozen=True)
class N8nWebhookConfig:
    webhook_url: str | None
    secret: str | None
    timeout_seconds: int = 5
    max_attempts: int = 8
    allow_insecure_http: bool = False

    @classmethod
    def from_env(cls) -> "N8nWebhookConfig":
        url = os.environ.get("VIDTRANS_N8N_WEBHOOK_URL", "").strip() or None
        secret = os.environ.get("VIDTRANS_N8N_WEBHOOK_SECRET", "").strip() or None
        config = cls(
            webhook_url=url,
            secret=secret,
            timeout_seconds=_parse_bounded_int(
                os.environ.get("VIDTRANS_N8N_TIMEOUT_SECONDS"),
                default=5,
                minimum=1,
                maximum=30,
            ),
            max_attempts=_parse_bounded_int(
                os.environ.get("VIDTRANS_N8N_MAX_ATTEMPTS"),
                default=8,
                minimum=1,
                maximum=20,
            ),
            allow_insecure_http=os.environ.get(
                "VIDTRANS_N8N_ALLOW_INSECURE_HTTP", "0"
            ).strip().lower() in {"1", "true", "yes", "on"},
        )
        config.validate()
        return config

    @property
    def enabled(self) -> bool:
        return self.webhook_url is not None

    def validate(self) -> None:
        if self.webhook_url is None:
            return
        parsed = urlparse(self.webhook_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("VIDTRANS_N8N_WEBHOOK_URL must be an absolute HTTP(S) URL")
        if (
            parsed.scheme != "https"
            and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            and not self.allow_insecure_http
        ):
            raise ValueError("VIDTRANS_N8N_WEBHOOK_URL must use HTTPS outside localhost")
        if not self.secret:
            raise ValueError("VIDTRANS_N8N_WEBHOOK_SECRET is required when the webhook is enabled")


class WorkflowEventStore:
    """Durable PostgreSQL outbox for non-blocking workflow integrations."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = prepare_target(database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = connect(self.database_path)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    last_error TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_events_delivery "
                "ON workflow_events(status, available_at, created_at)"
            )
            connection.execute(
                "UPDATE workflow_events SET status = 'pending' WHERE status = 'sending'"
            )
            connection.commit()

    def enqueue(self, event_type: str, aggregate_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        event_id = str(uuid.uuid4())
        now = utc_now()
        event = {
            "version": 1,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": now,
            **dict(payload),
        }
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO workflow_events(
                    event_id, event_type, aggregate_id, payload_json,
                    status, attempts, available_at, created_at
                ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
                """,
                (event_id, event_type, aggregate_id, encoded, now, now),
            )
            connection.commit()
        return event

    def claim_next(self) -> dict[str, Any] | None:
        now = utc_now()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM workflow_events
                WHERE status = 'pending' AND available_at <= ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE workflow_events
                SET status = 'sending', attempts = attempts + 1
                WHERE event_id = ? AND status = 'pending'
                """,
                (row["event_id"],),
            )
            if updated.rowcount != 1:
                return None
            result = dict(row)
            result["attempts"] = int(row["attempts"]) + 1
            result["payload"] = json.loads(str(row["payload_json"]))
            connection.commit()
            return result

    def mark_delivered(self, event_id: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE workflow_events
                SET status = 'delivered', delivered_at = ?, last_error = NULL
                WHERE event_id = ?
                """,
                (utc_now(), event_id),
            )
            connection.commit()

    def mark_failed(self, event_id: str, *, attempts: int, max_attempts: int, error: str) -> None:
        status = "dead" if attempts >= max_attempts else "pending"
        delay_seconds = min(300, 2 ** min(attempts, 8))
        available_at = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE workflow_events
                SET status = ?, available_at = ?, last_error = ?
                WHERE event_id = ?
                """,
                (status, available_at, error[:500], event_id),
            )
            connection.commit()

    def get(self, event_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM workflow_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def summary(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) count FROM workflow_events GROUP BY status"
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        return {
            "pending": counts.get("pending", 0),
            "sending": counts.get("sending", 0),
            "delivered": counts.get("delivered", 0),
            "dead": counts.get("dead", 0),
            "total": sum(counts.values()),
        }

    def list_events(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        allowed_statuses = {"pending", "sending", "delivered", "dead"}
        if status is not None and status not in allowed_statuses:
            raise ValueError("Invalid workflow event status")
        where = " WHERE status = ?" if status else ""
        values: list[Any] = [status] if status else []
        with closing(self._connect()) as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM workflow_events{where}", values
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT event_id, event_type, aggregate_id, status, attempts,
                       available_at, created_at, delivered_at, last_error
                FROM workflow_events{where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*values, max(1, min(limit, 200)), max(0, offset)],
            ).fetchall()
        return [dict(row) for row in rows], total

    def replay_dead(self, event_id: str) -> bool:
        with closing(self._connect()) as connection:
            updated = connection.execute(
                """
                UPDATE workflow_events
                SET status = 'pending', attempts = 0, available_at = ?, last_error = NULL
                WHERE event_id = ? AND status = 'dead'
                """,
                (utc_now(), event_id),
            )
            connection.commit()
        return updated.rowcount == 1


class WorkflowEventSink:
    def __init__(self, store: WorkflowEventStore) -> None:
        self.store = store

    @staticmethod
    def public_job(job_id: str, job: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "job_id": job_id,
            **{field: job.get(field) for field in sorted(_PUBLIC_JOB_FIELDS)},
        }

    def emit(self, event_type: str, job_id: str, job: Mapping[str, Any]) -> dict[str, Any]:
        return self.store.enqueue(event_type, job_id, {"job": self.public_job(job_id, job)})

    def emit_transition(
        self,
        job_id: str,
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        before = before or {}
        event_types: list[str] = []
        step = str(after.get("step") or "")
        if step != str(before.get("step") or ""):
            if step == "source-ready":
                event_types.append(
                    "download.completed" if after.get("source_platform") else "source.ready"
                )
            elif step in _STEP_EVENTS:
                event_types.append(_STEP_EVENTS[step])
        status = str(after.get("status") or "")
        if status != str(before.get("status") or "") and status in _STATUS_EVENTS:
            event_types.append(_STATUS_EVENTS[status])
        return [self.emit(event_type, job_id, after) for event_type in event_types]


class N8nWebhookClient:
    def __init__(self, config: N8nWebhookConfig) -> None:
        self.config = config

    def send(self, event: Mapping[str, Any]) -> None:
        if not self.config.webhook_url or not self.config.secret:
            return
        body = json.dumps(dict(event), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        signature = hmac.new(
            self.config.secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        request = urllib.request.Request(
            self.config.webhook_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "VidTrans-n8n/1",
                "X-VidTrans-Event-Id": str(event["event_id"]),
                "X-VidTrans-Timestamp": timestamp,
                "X-VidTrans-Signature": f"sha256={signature}",
                "Authorization": f"Bearer {self.config.secret}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                if not 200 <= int(response.status) < 300:
                    raise RuntimeError(f"n8n webhook returned HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"n8n webhook returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("n8n webhook is unavailable") from exc


class N8nWorkflowDispatcher:
    def __init__(
        self,
        store: WorkflowEventStore,
        client: N8nWebhookClient,
        *,
        poll_interval: float = 1.0,
    ) -> None:
        self.store = store
        self.client = client
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None or not self.client.config.enabled:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="n8n-workflow-dispatcher")
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
            event = await asyncio.to_thread(self.store.claim_next)
            if event is not None:
                try:
                    await asyncio.to_thread(self.client.send, event["payload"])
                except Exception as exc:
                    logger.warning(
                        "Unable to deliver workflow event %s to n8n: %s",
                        event["event_id"],
                        exc,
                    )
                    await asyncio.to_thread(
                        self.store.mark_failed,
                        str(event["event_id"]),
                        attempts=int(event["attempts"]),
                        max_attempts=self.client.config.max_attempts,
                        error=str(exc),
                    )
                else:
                    await asyncio.to_thread(self.store.mark_delivered, str(event["event_id"]))
                continue

            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass
