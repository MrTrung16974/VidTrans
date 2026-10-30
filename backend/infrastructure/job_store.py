from __future__ import annotations

import json
import sqlite3
import uuid

from infrastructure.database import connect, prepare_target
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


class JobStoreError(RuntimeError):
    """Base error raised by the persistent job store."""


class JobAlreadyExistsError(JobStoreError):
    pass


class JobNotFoundError(JobStoreError):
    pass


class JobStateConflictError(JobStoreError):
    """Raised when a mutation is not allowed in the job's current status."""


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class JobStore:
    """Small, process-safe JSON job repository backed by PostgreSQL.

    A new database connection is used for every operation. This avoids sharing a
    connection between FastAPI request threads and background worker threads.
    Updates use explicit transactions so read/merge/write is atomic.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = prepare_target(database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = connect(self.database_path)
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
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_updated ON jobs(status, updated_at)"
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            for name, definition in {
                "batch_id": "TEXT",
                "owner_user_id": "TEXT CHECK(owner_user_id IS NULL OR TRIM(owner_user_id) <> '')",
                "priority": "INTEGER NOT NULL DEFAULT 0",
                "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
                "worker_id": "TEXT",
                "scheduled_for": "TEXT",
                "deleted_at": "TEXT",
            }.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            connection.execute("UPDATE jobs SET owner_user_id='__legacy__' WHERE owner_user_id IS NULL OR owner_user_id=''")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, priority DESC, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_schedule ON jobs(status, scheduled_for)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_batch_updated ON jobs(batch_id, updated_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_owner_updated ON jobs(owner_user_id, updated_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_owner_created ON jobs(owner_user_id, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_owner_status ON jobs(owner_user_id, status, updated_at DESC)"
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_jobs_owner_insert
                BEFORE INSERT ON jobs
                FOR EACH ROW
                WHEN NEW.owner_user_id IS NULL OR TRIM(NEW.owner_user_id) = ''
                BEGIN
                    SELECT RAISE(ABORT, 'owner_user_id cannot be null or empty');
                END;
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_jobs_owner_update
                BEFORE UPDATE OF owner_user_id ON jobs
                FOR EACH ROW
                WHEN NEW.owner_user_id IS NULL OR TRIM(NEW.owner_user_id) = ''
                BEGIN
                    SELECT RAISE(ABORT, 'owner_user_id cannot be null or empty');
                END;
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_jobs_owner_immutable
                BEFORE UPDATE OF owner_user_id ON jobs
                FOR EACH ROW
                WHEN OLD.owner_user_id != '__legacy__' AND NEW.owner_user_id != OLD.owner_user_id
                BEGIN
                    SELECT RAISE(ABORT, 'owner_user_id is immutable');
                END;
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_batches_updated ON batches(updated_at DESC)"
            )
            batch_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(batches)").fetchall()
            }
            if "owner_user_id" not in batch_columns:
                connection.execute(
                    "ALTER TABLE batches ADD COLUMN owner_user_id TEXT "
                    "CHECK(owner_user_id IS NULL OR TRIM(owner_user_id) <> '')"
                )
            connection.execute("UPDATE batches SET owner_user_id='__legacy__' WHERE owner_user_id IS NULL OR owner_user_id=''")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_batches_owner_updated ON batches(owner_user_id, updated_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_batches_owner_created ON batches(owner_user_id, created_at DESC)"
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_batches_owner_insert
                BEFORE INSERT ON batches
                FOR EACH ROW
                WHEN NEW.owner_user_id IS NULL OR TRIM(NEW.owner_user_id) = ''
                BEGIN
                    SELECT RAISE(ABORT, 'owner_user_id cannot be null or empty');
                END;
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_batches_owner_update
                BEFORE UPDATE OF owner_user_id ON batches
                FOR EACH ROW
                WHEN NEW.owner_user_id IS NULL OR TRIM(NEW.owner_user_id) = ''
                BEGIN
                    SELECT RAISE(ABORT, 'owner_user_id cannot be null or empty');
                END;
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_batches_owner_immutable
                BEFORE UPDATE OF owner_user_id ON batches
                FOR EACH ROW
                WHEN OLD.owner_user_id != '__legacy__' AND NEW.owner_user_id != OLD.owner_user_id
                BEGIN
                    SELECT RAISE(ABORT, 'owner_user_id is immutable');
                END;
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_jobs_batch_owner_insert
                BEFORE INSERT ON jobs
                FOR EACH ROW
                WHEN NEW.batch_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM batches b
                    WHERE b.batch_id=NEW.batch_id AND b.owner_user_id=NEW.owner_user_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'job and batch owners must match');
                END;
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_jobs_batch_owner_update
                BEFORE UPDATE OF batch_id, owner_user_id ON jobs
                FOR EACH ROW
                WHEN NEW.batch_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM batches b
                    WHERE b.batch_id=NEW.batch_id AND b.owner_user_id=NEW.owner_user_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'job and batch owners must match');
                END;
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    owner_user_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    idem_key TEXT NOT NULL,
                    claim_token TEXT NOT NULL,
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (owner_user_id, scope, idem_key)
                )
                """
            )

    @staticmethod
    def _encode(payload: Mapping[str, Any]) -> str:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            default=_json_default,
        )

    @staticmethod
    def _decode(raw_payload: str) -> dict[str, Any]:
        try:
            value = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise JobStoreError("Stored job payload is not valid JSON") from exc
        if not isinstance(value, dict):
            raise JobStoreError("Stored job payload must be a JSON object")
        return value

    def create(self, job_id: str, payload: Mapping[str, Any], *, owner_user_id: str = "__legacy__") -> dict[str, Any]:
        if not owner_user_id.strip():
            raise JobStoreError("owner_user_id is required")
        normalized = dict(payload)
        normalized.setdefault("status", "queued")
        now = _utc_now()
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO jobs(job_id, status, payload_json, owner_user_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        str(normalized["status"]),
                        self._encode(normalized),
                        owner_user_id,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise JobAlreadyExistsError(f"Job already exists: {job_id}") from exc
        result = dict(normalized)
        if owner_user_id != "__legacy__":
            result["owner_user_id"] = owner_user_id
        return result

    def create_batch(
        self,
        batch_id: str,
        *,
        name: str,
        config: Mapping[str, Any],
        status: str = "queued",
        owner_user_id: str = "__legacy__",
    ) -> dict[str, Any]:
        if not owner_user_id.strip():
            raise JobStoreError("owner_user_id is required")
        now = _utc_now()
        payload = dict(config)
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO batches(batch_id, name, status, config_json, owner_user_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (batch_id, name, status, self._encode(payload), owner_user_id, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise JobAlreadyExistsError(f"Batch already exists: {batch_id}") from exc
        return {
            "batch_id": batch_id,
            "name": name,
            "status": status,
            "owner_user_id": owner_user_id,
            "config": payload,
            "created_at": now,
            "updated_at": now,
        }

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json,owner_user_id FROM jobs WHERE job_id = ? AND deleted_at IS NULL",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        payload = self._decode(row["payload_json"])
        if str(row["owner_user_id"]) != "__legacy__":
            payload["owner_user_id"] = str(row["owner_user_id"])
        return payload

    def get_for_user(self, job_id: str, owner_user_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json,owner_user_id FROM jobs WHERE job_id=? AND owner_user_id=? AND deleted_at IS NULL",
                (job_id, owner_user_id),
            ).fetchone()
        if row is None:
            return None
        payload = self._decode(row["payload_json"])
        payload["owner_user_id"] = str(row["owner_user_id"])
        return payload

    def owns_artifact(self, owner_user_id: str, filename: str) -> bool:
        artifact_fields = (
            "output_video", "subtitle_file", "translation_file",
            "tiktok_json_file", "tiktok_text_file", "filename",
        )
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM jobs WHERE owner_user_id=? AND deleted_at IS NULL AND payload_json LIKE ?",
                (owner_user_id, f"%{filename}%"),
            ).fetchall()
        return any(
            filename in {str(payload.get(field) or ""), Path(str(payload.get(field) or "")).name}
            for row in rows
            for payload in (self._decode(row["payload_json"]),)
            for field in artifact_fields
        )

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        if row is None:
            return None
        return self._batch_row(row)

    def get_batch_for_user(self, batch_id: str, owner_user_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM batches WHERE batch_id=? AND owner_user_id=?",
                (batch_id, owner_user_id),
            ).fetchone()
        return self._batch_row(row) if row is not None else None

    def list_jobs(
        self,
        *,
        status: str | None = None,
        batch_id: str | None = None,
        search: str | None = None,
        job_action: str | None = None,
        limit: int = 50,
        offset: int = 0,
        owner_user_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = ["deleted_at IS NULL"]
        values: list[Any] = []
        if owner_user_id is not None:
            clauses.append("owner_user_id = ?")
            values.append(owner_user_id)
        if status:
            clauses.append("status = ?")
            values.append(status)
        if batch_id:
            clauses.append("batch_id = ?")
            values.append(batch_id)
        if job_action:
            clauses.append("(payload_json LIKE ? OR payload_json LIKE ?)")
            values.extend([f'%"job_action":"{job_action}"%', f'%"job_action": "{job_action}"%'])
        if search:
            clauses.append("payload_json LIKE ?")
            values.append(f"%{search}%")
        where = f" WHERE {' AND '.join(clauses)}"
        with self._connection() as connection:
            total = int(
                connection.execute(f"SELECT COUNT(*) FROM jobs{where}", values).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*values, max(1, min(limit, 200)), max(0, offset)],
            ).fetchall()
        return [self._job_row(row) for row in rows], total

    def status_counts(self, *, owner_user_id: str | None = None) -> dict[str, int]:
        where = " WHERE deleted_at IS NULL" + (" AND owner_user_id = ?" if owner_user_id is not None else "")
        values: list[Any] = [owner_user_id] if owner_user_id is not None else []
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT status, COUNT(*) count FROM jobs{where} GROUP BY status",
                values,
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def list_batches(self, *, limit: int = 50, offset: int = 0, owner_user_id: str | None = None) -> tuple[list[dict[str, Any]], int]:
        with self._connection() as connection:
            where = " WHERE owner_user_id=?" if owner_user_id is not None else ""
            values: list[Any] = [owner_user_id] if owner_user_id is not None else []
            total = int(connection.execute(f"SELECT COUNT(*) FROM batches{where}", values).fetchone()[0])
            rows = connection.execute(
                f"SELECT * FROM batches{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*values, max(1, min(limit, 200)), max(0, offset)),
            ).fetchall()
            batches = [self._batch_row(row) for row in rows]
            for batch in batches:
                counts = connection.execute(
                    "SELECT status, COUNT(*) count FROM jobs "
                    "WHERE batch_id=? AND owner_user_id=? AND deleted_at IS NULL GROUP BY status",
                    (batch["batch_id"], batch["owner_user_id"]),
                ).fetchall()
                batch["counts"] = {str(row["status"]): int(row["count"]) for row in counts}
                batch["total_jobs"] = sum(batch["counts"].values())
                active = sum(
                    batch["counts"].get(status, 0)
                    for status in ("queued", "processing", "scheduled", "cancelling")
                )
                if active:
                    batch["status"] = "processing"
                elif batch["total_jobs"] and batch["counts"].get("completed", 0) == batch["total_jobs"]:
                    batch["status"] = "completed"
                elif batch["total_jobs"]:
                    batch["status"] = "completed_with_errors"
        return batches, total

    def claim_idempotency_key(
        self, owner_user_id: str, scope: str, key: str, *, pending_ttl_seconds: int, ttl_seconds: int
    ) -> tuple[str, Any]:
        """Claim ``key`` for one create request.

        Returns ``("claimed", token)`` when the caller must do the work, ``("done", response)``
        when an earlier request finished, or ``("pending", None)`` while it still runs. A claim
        whose request died without releasing it expires after ``pending_ttl_seconds``.
        """

        now = datetime.now(timezone.utc)
        token = uuid.uuid4().hex
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM idempotency_keys WHERE owner_user_id=? AND scope=? AND idem_key=? "
                "AND (created_at < ? OR (response_json IS NULL AND created_at < ?))",
                (
                    owner_user_id, scope, key,
                    (now - timedelta(seconds=ttl_seconds)).isoformat(),
                    (now - timedelta(seconds=pending_ttl_seconds)).isoformat(),
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO idempotency_keys(owner_user_id, scope, idem_key, claim_token, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (owner_user_id, scope, key, token, now.isoformat()),
            )
            row = connection.execute(
                "SELECT claim_token, response_json FROM idempotency_keys WHERE owner_user_id=? AND scope=? AND idem_key=?",
                (owner_user_id, scope, key),
            ).fetchone()
        if row is None or str(row["claim_token"]) == token:
            return "claimed", token
        if row["response_json"] is None:
            return "pending", None
        return "done", self._decode(str(row["response_json"]))

    def reclaim_idempotency_key(self, owner_user_id: str, scope: str, key: str) -> str:
        """Take over a finished key whose result is no longer worth replaying (e.g. it failed)."""

        token = uuid.uuid4().hex
        with self._connection() as connection:
            connection.execute(
                "UPDATE idempotency_keys SET claim_token=?, response_json=NULL, created_at=? "
                "WHERE owner_user_id=? AND scope=? AND idem_key=?",
                (token, _utc_now(), owner_user_id, scope, key),
            )
        return token

    def complete_idempotency_key(
        self, owner_user_id: str, scope: str, key: str, token: str, response: Mapping[str, Any]
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE idempotency_keys SET response_json=? "
                "WHERE owner_user_id=? AND scope=? AND idem_key=? AND claim_token=?",
                (self._encode(response), owner_user_id, scope, key, token),
            )

    def release_idempotency_key(self, owner_user_id: str, scope: str, key: str, token: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM idempotency_keys WHERE owner_user_id=? AND scope=? AND idem_key=? AND claim_token=?",
                (owner_user_id, scope, key, token),
            )

    @staticmethod
    def _batch_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "batch_id": str(row["batch_id"]),
            "name": str(row["name"]),
            "status": str(row["status"]),
            "owner_user_id": str(row["owner_user_id"]),
            "config": JobStore._decode(str(row["config_json"])),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = JobStore._decode(str(row["payload_json"]))
        return {
            "job_id": str(row["job_id"]),
            "batch_id": row["batch_id"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            **payload,
            "owner_user_id": str(row["owner_user_id"]),
        }

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(f"Job not found: {job_id}")

            payload = self._decode(row["payload_json"])
            current_status = str(payload.get("status", "queued"))
            requested_status = fields.get("status")
            if current_status == "cancelled" and requested_status != "cancelled":
                return payload
            if current_status == "cancelling" and requested_status != "cancelled":
                return payload
            payload.update(fields)
            status = str(payload.get("status", "queued"))
            scheduled_for = (
                str(payload.get("tiktok_publish_at"))
                if status == "scheduled" and payload.get("tiktok_publish_at")
                else None
            )
            
            set_clause = "SET status = ?, payload_json = ?, scheduled_for = ?, updated_at = ?"
            params = [status, self._encode(payload), scheduled_for, now]
            
            if "cancel_requested" in fields:
                set_clause += ", cancel_requested = ?"
                params.append(1 if fields["cancel_requested"] else 0)
                
            params.append(job_id)
            connection.execute(f"UPDATE jobs {set_clause} WHERE job_id = ?", tuple(params))
        return payload

    def attach_to_batch(self, job_id: str, batch_id: str, *, priority: int = 0) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json, owner_user_id FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(f"Job not found: {job_id}")
            batch = connection.execute("SELECT owner_user_id FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if batch is not None and str(batch["owner_user_id"]) != str(row["owner_user_id"]):
                raise JobStoreError("Job and batch owners do not match")
            payload = self._decode(row["payload_json"])
            payload["batch_id"] = batch_id
            connection.execute(
                "UPDATE jobs SET batch_id = ?, priority = ?, payload_json = ? WHERE job_id = ?",
                (batch_id, priority, self._encode(payload), job_id),
            )

    def backfill_legacy_owner(self, owner_user_id: str) -> None:
        if not owner_user_id.strip():
            raise JobStoreError("owner_user_id is required")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # A job linked to a batch is protected by
            # trg_jobs_batch_owner_update. Migrate the parent batch first so
            # the child job continues to match throughout the transaction.
            # The previous order made startup fail on every pre-tenant batch.
            connection.execute(
                "UPDATE batches SET owner_user_id=? WHERE owner_user_id='__legacy__'",
                (owner_user_id,),
            )
            rows = connection.execute("SELECT job_id,payload_json FROM jobs WHERE owner_user_id='__legacy__'").fetchall()
            for row in rows:
                payload = self._decode(row["payload_json"])
                connection.execute(
                    "UPDATE jobs SET owner_user_id=?,payload_json=? WHERE job_id=?",
                    (owner_user_id, self._encode(payload), row["job_id"]),
                )

    def claim_next(self, worker_id: str) -> tuple[str, dict[str, Any]] | None:
        """Atomically claim the oldest highest-priority queued job."""

        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT job_id, payload_json
                FROM jobs
                WHERE cancel_requested = 0
                  AND deleted_at IS NULL
                  AND (
                    status = 'queued'
                    OR (status = 'scheduled' AND scheduled_for IS NOT NULL AND scheduled_for <= ?)
                  )
                ORDER BY
                  CASE WHEN status = 'scheduled' THEN 0 ELSE 1 END,
                  priority DESC,
                  COALESCE(scheduled_for, created_at) ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None
            payload = self._decode(row["payload_json"])
            is_scheduled_publish = payload.get("job_action") == "publish_tiktok"
            payload.update(
                {
                    "status": "processing",
                    "step": (
                        "publishing-tiktok"
                        if is_scheduled_publish
                        else payload.get("step") if payload.get("step") != "queued" else "starting"
                    ),
                    "worker_id": worker_id,
                    "started_at": payload.get("started_at") or now,
                    "cancel_requested": False,
                }
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = 'processing', payload_json = ?, worker_id = ?, scheduled_for = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (self._encode(payload), worker_id, now, row["job_id"]),
            )
        return str(row["job_id"]), payload

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, payload_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(f"Job not found: {job_id}")
            payload = self._decode(row["payload_json"])
            status = str(row["status"])
            if status in {"completed", "failed", "cancelled"}:
                return payload
            if status == "cancelling":
                return payload
            stops_immediately = status in {"queued", "scheduled"}
            next_status = "cancelled" if stops_immediately else "cancelling"
            if stops_immediately:
                step_detail = "Đã hủy theo yêu cầu"
            elif payload.get("step") == "publishing-tiktok":
                # An upload already handed to TikTok cannot be recalled.
                step_detail = (
                    "Đang dừng; video có thể đã được gửi tới TikTok và không thể thu hồi tự động"
                )
            else:
                step_detail = "Đang dừng tiến trình an toàn"
            payload.update(
                {
                    "status": next_status,
                    "step": next_status,
                    "step_detail": step_detail,
                    "cancel_requested": True,
                }
            )
            if stops_immediately:
                payload["finished_at"] = now
            else:
                payload["finished_at"] = None
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, cancel_requested = 1, payload_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (next_status, self._encode(payload), now, job_id),
            )
        return payload

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise JobNotFoundError(f"Job not found: {job_id}")
        return bool(row["cancel_requested"])

    def queue_positions(self) -> dict[str, int]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT job_id FROM jobs
                WHERE status = 'queued' AND cancel_requested = 0 AND deleted_at IS NULL
                ORDER BY priority DESC, created_at ASC
                """
            ).fetchall()
        return {str(row["job_id"]): index for index, row in enumerate(rows, start=1)}

    def delete(self, job_id: str, *, require_terminal: bool = False) -> dict[str, Any]:
        """Soft-delete a job: hide it from every read but keep its row and files.

        The row is purged, together with its storage, by ``prune_terminal`` once
        the retention window counted from the deletion has passed.
        """
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, payload_json FROM jobs WHERE job_id = ? AND deleted_at IS NULL",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(f"Job not found: {job_id}")
            if require_terminal and str(row["status"]) not in TERMINAL_STATUSES:
                raise JobStateConflictError(f"Job is still active: {job_id}")
            payload = self._decode(row["payload_json"])
            connection.execute(
                "UPDATE jobs SET deleted_at = ?, updated_at = ? WHERE job_id = ?",
                (now, now, job_id),
            )
        return payload

    def retry(self, job_id: str, **fields: Any) -> dict[str, Any]:
        """Atomically return a terminal job to the queue.

        ``update`` intentionally ignores writes to cancelled jobs so late
        workers cannot revive them; retry is the only path out of a terminal
        status and it refuses jobs whose worker has not acknowledged yet.
        """

        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, payload_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(f"Job not found: {job_id}")
            if str(row["status"]) not in TERMINAL_STATUSES:
                raise JobStateConflictError(f"Job is still active: {job_id}")
            payload = self._decode(row["payload_json"])
            payload.update(fields)
            payload.update({"status": "queued", "cancel_requested": False, "worker_id": None})
            connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', cancel_requested = 0, worker_id = NULL,
                    scheduled_for = NULL, payload_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (self._encode(payload), now, job_id),
            )
        return payload

    def finalize_cancellation(
        self, job_id: str, **fields: Any
    ) -> tuple[dict[str, Any], bool]:
        """Move a ``cancelling`` job to ``cancelled`` once its worker stopped.

        Returns the payload and whether this call performed the transition, so
        callers can release quota exactly once.
        """

        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, payload_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(f"Job not found: {job_id}")
            payload = self._decode(row["payload_json"])
            if str(row["status"]) != "cancelling":
                return payload, False
            payload.update(
                {
                    "step": "cancelled",
                    "step_detail": "Đã hủy theo yêu cầu",
                    **fields,
                    "status": "cancelled",
                    "cancel_requested": True,
                    "finished_at": fields.get("finished_at") or now,
                }
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', cancel_requested = 1, payload_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (self._encode(payload), now, job_id),
            )
        return payload, True

    def prune_terminal(
        self,
        cutoff: str,
        *,
        owner_user_id: str | None = None,
        exclude_owner_user_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Delete terminal jobs older than cutoff and return their storage identity.

        ``owner_user_id`` limits pruning to one tenant; ``exclude_owner_user_ids``
        skips tenants governed by their own retention policy. Jobs with a social
        publish attempt that has not finished yet are kept so scheduled posts
        never lose their video.
        """
        terminal = ("completed", "failed", "cancelled")
        excluded = sorted({str(item) for item in exclude_owner_user_ids if str(item)})
        owner_clause, owner_values = "", []
        if owner_user_id is not None:
            owner_clause = " AND owner_user_id=?"
            owner_values.append(owner_user_id)
        if excluded:
            owner_clause += f" AND owner_user_id NOT IN ({','.join('?' * len(excluded))})"
            owner_values.extend(excluded)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            publish_clause = ""
            for table in ("social_publish_attempts",):
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if exists is not None:
                    publish_clause += (
                        f" AND NOT EXISTS (SELECT 1 FROM {table} p WHERE p.job_id=jobs.job_id "
                        "AND p.status NOT IN ('PUBLISH_COMPLETE','SEND_TO_USER_INBOX','FAILED','CANCELLED'))"
                    )
            rows = connection.execute(
                "SELECT job_id,owner_user_id,payload_json FROM jobs "
                f"WHERE status IN (?,?,?) AND updated_at < ?{owner_clause}{publish_clause}",
                (*terminal, cutoff, *owner_values),
            ).fetchall()
            removed = [
                {
                    "job_id": str(row["job_id"]),
                    "owner_user_id": str(row["owner_user_id"]),
                    "payload": self._decode(str(row["payload_json"])),
                }
                for row in rows
            ]
            if rows:
                connection.executemany(
                    "DELETE FROM jobs WHERE job_id=?",
                    [(str(row["job_id"]),) for row in rows],
                )
            batch_filter = (
                f"WHERE updated_at < ?{owner_clause} "
                "AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.batch_id=batches.batch_id)"
            )
            orphan_batches = connection.execute(
                f"SELECT batch_id,owner_user_id FROM batches {batch_filter}",
                (cutoff, *owner_values),
            ).fetchall()
            connection.execute(f"DELETE FROM batches {batch_filter}", (cutoff, *owner_values))
        return {
            "jobs": removed,
            "batches": [
                {"batch_id": str(row["batch_id"]), "owner_user_id": str(row["owner_user_id"])}
                for row in orphan_batches
            ],
        }

    def recover_interrupted(self, reason: str = "Server restarted while the job was running") -> int:
        """Mark jobs with no surviving in-process task as failed.

        Call this only in a single-process deployment or from a queue coordinator.
        The store itself does not invoke recovery because another worker process
        may still own an active job; the application explicitly opts in at startup.
        """

        now = _utc_now()
        recovered = 0
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT job_id, payload_json FROM jobs "
                "WHERE status IN ('queued', 'processing', 'cancelling')"
            ).fetchall()
            for row in rows:
                payload = self._decode(row["payload_json"])
                if payload.get("cancel_requested") or payload.get("status") == "cancelling":
                    payload.update(
                        {
                            "status": "cancelled",
                            "step": "cancelled",
                            "step_detail": "Đã hủy sau khi worker dừng",
                            "finished_at": now,
                        }
                    )
                else:
                    payload.update(
                        {
                            "status": "failed",
                            "step": "interrupted",
                            "error": reason,
                        }
                    )
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, payload_json = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (str(payload["status"]), self._encode(payload), now, row["job_id"]),
                )
                recovered += 1
        return recovered

    def requeue_interrupted(
        self, max_restarts: int | None = None
    ) -> list[tuple[str, dict[str, Any]]]:
        """Atomically return unfinished jobs to the queue after a process restart.

        The caller owns actual task scheduling.  Keeping this operation in the
        store prevents a concurrent status request from observing a half-updated
        job payload.  Only jobs that were running count as restarted; one that
        has already been restarted ``max_restarts`` times is marked failed
        instead, so a job that crashes the worker cannot loop forever.  The
        returned payloads carry either ``queued`` or ``failed`` status.
        """

        now = _utc_now()
        requeued: list[tuple[str, dict[str, Any]]] = []
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT job_id, payload_json FROM jobs "
                "WHERE status IN ('queued', 'processing', 'cancelling')"
            ).fetchall()
            for row in rows:
                payload = self._decode(row["payload_json"])
                if payload.get("cancel_requested") or payload.get("status") == "cancelling":
                    payload.update(
                        {
                            "status": "cancelled",
                            "step": "cancelled",
                            "step_detail": "Đã hủy sau khi worker dừng",
                            "finished_at": now,
                        }
                    )
                    connection.execute(
                        "UPDATE jobs SET status='cancelled', payload_json=?, updated_at=? "
                        "WHERE job_id=?",
                        (self._encode(payload), now, row["job_id"]),
                    )
                    continue
                restart_count = int(payload.get("restart_count", 0))
                if payload.get("status") == "processing":
                    restart_count += 1
                if max_restarts is not None and restart_count > max_restarts:
                    payload.update(
                        {
                            "status": "failed",
                            "step": "failed",
                            "restart_count": restart_count,
                            "error": (
                                f"Server stopped while running this job {restart_count} times "
                                "(possibly out of memory); not restarting it again"
                            ),
                            "finished_at": now,
                        }
                    )
                else:
                    payload.update(
                        {
                            "status": "queued",
                            "step": "queued",
                            "progress": 0.0,
                            "step_detail": "Server restarted; restarting the job from the beginning",
                            "restart_count": restart_count,
                        }
                    )
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, payload_json = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (str(payload["status"]), self._encode(payload), now, row["job_id"]),
                )
                requeued.append((str(row["job_id"]), payload))
        return requeued


# Backward-compatible import for isolated SQLite repository tests.
SQLiteJobStore = JobStore
