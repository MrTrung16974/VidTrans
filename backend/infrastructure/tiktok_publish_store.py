from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TikTokPublishStoreError(RuntimeError):
    pass


class TikTokPublishNotFoundError(TikTokPublishStoreError):
    pass


class SQLiteTikTokPublishStore:
    """Durable, process-safe store for TikTok remote side effects."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tiktok_publish_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    job_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scheduled_for TEXT,
                    payload_json TEXT NOT NULL,
                    remote_publish_id TEXT,
                    claimed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_tiktok_publish_due "
                "ON tiktok_publish_attempts(status, scheduled_for)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_tiktok_publish_job "
                "ON tiktok_publish_attempts(job_id, created_at DESC)"
            )

    @staticmethod
    def _encode(payload: Mapping[str, Any]) -> str:
        return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decode(value: str) -> dict[str, Any]:
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise TikTokPublishStoreError("Stored publish payload must be an object")
        return payload

    @classmethod
    def _row(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "attempt_id": str(row["attempt_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            "job_id": str(row["job_id"]),
            "mode": str(row["mode"]),
            "status": str(row["status"]),
            "scheduled_for": row["scheduled_for"],
            "remote_publish_id": row["remote_publish_id"],
            "claimed_at": row["claimed_at"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            **cls._decode(str(row["payload_json"])),
        }

    def create(
        self,
        *,
        idempotency_key: str,
        job_id: str,
        mode: str,
        status: str,
        scheduled_for: str | None,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        attempt_id = uuid.uuid4().hex
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO tiktok_publish_attempts(
                        attempt_id, idempotency_key, job_id, mode, status,
                        scheduled_for, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        idempotency_key,
                        job_id,
                        mode,
                        status,
                        scheduled_for,
                        self._encode(payload),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            existing = self.get_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            if existing["job_id"] != job_id:
                raise TikTokPublishStoreError(
                    "Idempotency key đã được dùng cho một video khác"
                )
            return existing, False
        created = self.get(attempt_id)
        assert created is not None
        return created, True

    def get(self, attempt_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM tiktok_publish_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        return self._row(row) if row else None

    def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM tiktok_publish_attempts WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        return self._row(row) if row else None

    def latest_for_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM tiktok_publish_attempts
                WHERE job_id = ? ORDER BY created_at DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        return self._row(row) if row else None

    def update(self, attempt_id: str, **fields: Any) -> dict[str, Any]:
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM tiktok_publish_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise TikTokPublishNotFoundError(attempt_id)
            payload = self._decode(str(row["payload_json"]))
            column_fields: dict[str, Any] = {}
            for key, value in fields.items():
                if key in {"status", "scheduled_for", "remote_publish_id", "claimed_at"}:
                    column_fields[key] = value
                else:
                    payload[key] = value
            assignments = ["payload_json = ?", "updated_at = ?"]
            values: list[Any] = [self._encode(payload), now]
            for key, value in column_fields.items():
                assignments.append(f"{key} = ?")
                values.append(value)
            values.append(attempt_id)
            connection.execute(
                f"UPDATE tiktok_publish_attempts SET {', '.join(assignments)} WHERE attempt_id = ?",
                values,
            )
        updated = self.get(attempt_id)
        assert updated is not None
        return updated

    def claim_due(self) -> dict[str, Any] | None:
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM tiktok_publish_attempts
                WHERE status = 'SCHEDULED_LOCAL' AND scheduled_for <= ?
                ORDER BY scheduled_for ASC LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE tiktok_publish_attempts
                SET status = 'UPLOADING', claimed_at = ?, updated_at = ?
                WHERE attempt_id = ? AND status = 'SCHEDULED_LOCAL'
                """,
                (now, now, row["attempt_id"]),
            )
        return self.get(str(row["attempt_id"]))

    def next_reconcilable(self, *, min_age_seconds: float = 10.0) -> dict[str, Any] | None:
        before = (datetime.now(timezone.utc) - timedelta(seconds=min_age_seconds)).isoformat()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM tiktok_publish_attempts
                WHERE remote_publish_id IS NOT NULL
                  AND status IN (
                    'UPLOADING', 'PROCESSING_UPLOAD', 'PROCESSING_DOWNLOAD',
                    'SUBMITTED', 'NEEDS_RECONCILIATION'
                  )
                  AND updated_at <= ?
                ORDER BY updated_at ASC LIMIT 1
                """,
                (before,),
            ).fetchone()
        return self._row(row) if row else None

    def cancel(self, attempt_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE tiktok_publish_attempts
                SET status = 'CANCELLED', scheduled_for = NULL, updated_at = ?
                WHERE attempt_id = ? AND status IN ('READY', 'SCHEDULED_LOCAL')
                """,
                (now, attempt_id),
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    "SELECT status FROM tiktok_publish_attempts WHERE attempt_id = ?",
                    (attempt_id,),
                ).fetchone()
                if row is None:
                    raise TikTokPublishNotFoundError(attempt_id)
                raise TikTokPublishStoreError(
                    f"Không thể hủy yêu cầu đang ở trạng thái {row['status']}"
                )
        cancelled = self.get(attempt_id)
        assert cancelled is not None
        return cancelled
