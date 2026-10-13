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


class SocialPublishStoreError(RuntimeError):
    pass


class SocialPublishNotFoundError(SocialPublishStoreError):
    pass


class SQLiteSocialPublishStore:
    """Durable store for normalized multi-platform publishing attempts."""

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
                CREATE TABLE IF NOT EXISTS social_publish_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scheduled_for TEXT,
                    idempotency_key TEXT NOT NULL,
                    remote_media_id TEXT,
                    remote_post_id TEXT,
                    payload_json TEXT NOT NULL,
                    status_detail_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    error_message TEXT,
                    claimed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(platform, account_id, idempotency_key)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_publish_due "
                "ON social_publish_attempts(status, scheduled_for)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_publish_job "
                "ON social_publish_attempts(job_id, platform, created_at DESC)"
            )

    @staticmethod
    def _encode(payload: Mapping[str, Any]) -> str:
        return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decode(value: str) -> dict[str, Any]:
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise SocialPublishStoreError("Stored publish data must be an object")
        return decoded

    @classmethod
    def _row(cls, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        payload = cls._decode(str(result.pop("payload_json")))
        result["status_detail"] = cls._decode(str(result.pop("status_detail_json")))
        result["account_id"] = result["account_id"] or None
        # Persisted columns are authoritative; payload must never shadow status,
        # remote IDs or other state-machine fields.
        return {**payload, **result}

    def migrate_legacy_tiktok_history(self) -> int:
        """Copy terminal legacy TikTok attempts once without claiming active work."""
        status_map = {
            "PUBLISH_COMPLETE": "PUBLISHED",
            "SEND_TO_USER_INBOX": "DRAFT_DELIVERED",
            "FAILED": "FAILED",
            "CANCELLED": "CANCELLED",
        }
        mode_map = {
            "DIRECT_NOW": "DIRECT_NOW",
            "SCHEDULE": "SCHEDULE_LOCAL",
            "INBOX_DRAFT": "DRAFT",
        }
        migrated = 0
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tiktok_publish_attempts'"
            ).fetchone()
            if exists is None:
                return 0
            rows = connection.execute(
                """
                SELECT * FROM tiktok_publish_attempts
                WHERE status IN ('PUBLISH_COMPLETE', 'SEND_TO_USER_INBOX', 'FAILED', 'CANCELLED')
                ORDER BY created_at ASC
                """
            ).fetchall()
            for row in rows:
                payload = self._decode(str(row["payload_json"]))
                status_detail = payload.pop("status_detail", {})
                error_message = payload.pop("error", None)
                account_id = str(payload.get("account_id") or "")
                for reserved in (
                    "attempt_id", "job_id", "platform", "account_id", "mode", "status",
                    "scheduled_for", "idempotency_key", "remote_media_id", "remote_post_id",
                    "error_code", "error_message", "claimed_at", "created_at", "updated_at",
                ):
                    payload.pop(reserved, None)
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO social_publish_attempts(
                        attempt_id, job_id, platform, account_id, mode, status,
                        scheduled_for, idempotency_key, remote_media_id, payload_json,
                        status_detail_json, error_message, claimed_at, created_at, updated_at
                    ) VALUES (?, ?, 'TIKTOK', ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"legacy-tiktok-{row['attempt_id']}",
                        row["job_id"],
                        account_id,
                        mode_map.get(str(row["mode"]), "DIRECT_NOW"),
                        status_map[str(row["status"])],
                        row["idempotency_key"],
                        row["remote_publish_id"],
                        self._encode(payload),
                        self._encode(status_detail if isinstance(status_detail, dict) else {}),
                        str(error_message)[:500] if error_message else None,
                        row["claimed_at"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
                migrated += int(cursor.rowcount == 1)
        return migrated

    def create(
        self,
        *,
        job_id: str,
        platform: str,
        account_id: str | None,
        mode: str,
        status: str,
        scheduled_for: str | None,
        idempotency_key: str,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        attempt_id = uuid.uuid4().hex
        normalized_account = (account_id or "").strip()
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO social_publish_attempts(
                        attempt_id, job_id, platform, account_id, mode, status,
                        scheduled_for, idempotency_key, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id, job_id, platform, normalized_account, mode, status,
                        scheduled_for, idempotency_key, self._encode(payload), now, now,
                    ),
                )
        except sqlite3.IntegrityError:
            existing = self.get_by_idempotency(platform, normalized_account, idempotency_key)
            if existing is None:
                raise
            if existing["job_id"] != job_id:
                raise SocialPublishStoreError(
                    "Idempotency key đã được dùng cho một video khác"
                )
            return existing, False
        created = self.get(attempt_id)
        assert created is not None
        return created, True

    def get(self, attempt_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM social_publish_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
        return self._row(row) if row else None

    def get_by_idempotency(
        self, platform: str, account_id: str | None, key: str
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM social_publish_attempts
                WHERE platform = ? AND account_id = ? AND idempotency_key = ?
                """,
                (platform, account_id or "", key),
            ).fetchone()
        return self._row(row) if row else None

    def list_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM social_publish_attempts
                WHERE job_id = ? ORDER BY created_at DESC
                """,
                (job_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def update(self, attempt_id: str, **fields: Any) -> dict[str, Any]:
        columns = {
            "status", "scheduled_for", "remote_media_id", "remote_post_id",
            "error_code", "error_message", "claimed_at", "status_detail",
        }
        unknown = set(fields) - columns
        if unknown:
            raise SocialPublishStoreError(f"Unsupported update fields: {sorted(unknown)}")
        now = utc_now()
        assignments = ["updated_at = ?"]
        values: list[Any] = [now]
        for key, value in fields.items():
            column = "status_detail_json" if key == "status_detail" else key
            assignments.append(f"{column} = ?")
            values.append(self._encode(value or {}) if key == "status_detail" else value)
        values.append(attempt_id)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"UPDATE social_publish_attempts SET {', '.join(assignments)} "
                "WHERE attempt_id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise SocialPublishNotFoundError(attempt_id)
        updated = self.get(attempt_id)
        assert updated is not None
        return updated

    def claim_due(self) -> dict[str, Any] | None:
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT attempt_id FROM social_publish_attempts
                WHERE status = 'SCHEDULED_LOCAL' AND scheduled_for <= ?
                ORDER BY scheduled_for ASC LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None
            cursor = connection.execute(
                """
                UPDATE social_publish_attempts
                SET status = 'CLAIMED', claimed_at = ?, updated_at = ?
                WHERE attempt_id = ? AND status = 'SCHEDULED_LOCAL'
                """,
                (now, now, row["attempt_id"]),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(str(row["attempt_id"]))

    def next_reconcilable(self, *, min_age_seconds: float = 10.0) -> dict[str, Any] | None:
        before = (datetime.now(timezone.utc) - timedelta(seconds=min_age_seconds)).isoformat()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM social_publish_attempts
                WHERE (remote_media_id IS NOT NULL OR remote_post_id IS NOT NULL)
                  AND status IN ('UPLOADING', 'PROCESSING_REMOTE', 'SUBMITTING',
                                 'NEEDS_RECONCILIATION')
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
                UPDATE social_publish_attempts
                SET status = 'CANCELLED', scheduled_for = NULL, updated_at = ?
                WHERE attempt_id = ? AND status IN ('READY', 'SCHEDULED_LOCAL')
                """,
                (now, attempt_id),
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    "SELECT status FROM social_publish_attempts WHERE attempt_id = ?",
                    (attempt_id,),
                ).fetchone()
                if row is None:
                    raise SocialPublishNotFoundError(attempt_id)
                raise SocialPublishStoreError(
                    f"Không thể hủy yêu cầu đang ở trạng thái {row['status']}"
                )
        cancelled = self.get(attempt_id)
        assert cancelled is not None
        return cancelled

