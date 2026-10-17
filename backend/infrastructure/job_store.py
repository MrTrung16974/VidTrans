from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


class JobStoreError(RuntimeError):
    """Base error raised by the persistent job store."""


class JobAlreadyExistsError(JobStoreError):
    pass


class JobNotFoundError(JobStoreError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class SQLiteJobStore:
    """Small, process-safe JSON job repository backed by SQLite.

    A new SQLite connection is used for every operation. This avoids sharing a
    connection between FastAPI request threads and background worker threads.
    Updates use ``BEGIN IMMEDIATE`` so read/merge/write is atomic.
    """

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
                "owner_user_id": "TEXT",
                "priority": "INTEGER NOT NULL DEFAULT 0",
                "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
                "worker_id": "TEXT",
                "scheduled_for": "TEXT",
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
                connection.execute("ALTER TABLE batches ADD COLUMN owner_user_id TEXT")
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
                "SELECT payload_json,owner_user_id FROM jobs WHERE job_id = ?",
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
                "SELECT payload_json,owner_user_id FROM jobs WHERE job_id=? AND owner_user_id=?",
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
                "SELECT payload_json FROM jobs WHERE owner_user_id=? AND payload_json LIKE ?",
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
        limit: int = 50,
        offset: int = 0,
        owner_user_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
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
        if search:
            clauses.append("payload_json LIKE ?")
            values.append(f"%{search}%")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as connection:
            total = int(
                connection.execute(f"SELECT COUNT(*) FROM jobs{where}", values).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*values, max(1, min(limit, 200)), max(0, offset)],
            ).fetchall()
        return [self._job_row(row) for row in rows], total

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
                    "SELECT status, COUNT(*) count FROM jobs WHERE batch_id=? AND owner_user_id=? GROUP BY status",
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

    @staticmethod
    def _batch_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "batch_id": str(row["batch_id"]),
            "name": str(row["name"]),
            "status": str(row["status"]),
            "owner_user_id": str(row["owner_user_id"]),
            "config": SQLiteJobStore._decode(str(row["config_json"])),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = SQLiteJobStore._decode(str(row["payload_json"]))
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
            rows = connection.execute("SELECT job_id,payload_json FROM jobs WHERE owner_user_id='__legacy__'").fetchall()
            for row in rows:
                payload = self._decode(row["payload_json"])
                connection.execute(
                    "UPDATE jobs SET owner_user_id=?,payload_json=? WHERE job_id=?",
                    (owner_user_id, self._encode(payload), row["job_id"]),
                )
            connection.execute("UPDATE batches SET owner_user_id=? WHERE owner_user_id='__legacy__'", (owner_user_id,))

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
            if status in {"queued", "ready", "scheduled"}:
                payload.update(
                    {
                        "status": "cancelled",
                        "step": "cancelled",
                        "cancel_requested": True,
                        "finished_at": now,
                    }
                )
                next_status = "cancelled"
            else:
                payload.update(
                    {
                        "status": "cancelling",
                        "step_detail": "Đang dừng an toàn sau công đoạn hiện tại",
                        "cancel_requested": True,
                    }
                )
                next_status = "cancelling"
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
                WHERE status = 'queued' AND cancel_requested = 0
                ORDER BY priority DESC, created_at ASC
                """
            ).fetchall()
        return {str(row["job_id"]): index for index, row in enumerate(rows, start=1)}

    def delete(self, job_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(f"Job not found: {job_id}")
            payload = self._decode(row["payload_json"])
            connection.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        return payload

    def prune_terminal(self, cutoff: str) -> dict[str, Any]:
        """Delete terminal jobs older than cutoff and return their storage identity."""
        terminal = ("completed", "failed", "cancelled")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT job_id,owner_user_id,payload_json FROM jobs "
                "WHERE status IN (?,?,?) AND updated_at < ?",
                (*terminal, cutoff),
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
            orphan_batches = connection.execute(
                "SELECT batch_id,owner_user_id FROM batches WHERE updated_at < ? "
                "AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.batch_id=batches.batch_id)",
                (cutoff,),
            ).fetchall()
            connection.execute(
                "DELETE FROM batches WHERE updated_at < ? "
                "AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.batch_id=batches.batch_id)",
                (cutoff,),
            )
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
                "SELECT job_id, payload_json FROM jobs WHERE status IN ('queued', 'processing')"
            ).fetchall()
            for row in rows:
                payload = self._decode(row["payload_json"])
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
                    SET status = 'failed', payload_json = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (self._encode(payload), now, row["job_id"]),
                )
                recovered += 1
        return recovered

    def requeue_interrupted(self) -> list[tuple[str, dict[str, Any]]]:
        """Atomically return unfinished jobs to the queue after a process restart.

        The caller owns actual task scheduling.  Keeping this operation in the
        store prevents a concurrent status request from observing a half-updated
        job payload.
        """

        now = _utc_now()
        requeued: list[tuple[str, dict[str, Any]]] = []
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT job_id, payload_json FROM jobs WHERE status IN ('queued', 'processing')"
            ).fetchall()
            for row in rows:
                payload = self._decode(row["payload_json"])
                payload.update(
                    {
                        "status": "queued",
                        "step": "queued",
                        "progress": 0.0,
                        "step_detail": "Server restarted; restarting the job from the beginning",
                        "restart_count": int(payload.get("restart_count", 0)) + 1,
                    }
                )
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued', payload_json = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (self._encode(payload), now, row["job_id"]),
                )
                requeued.append((str(row["job_id"]), payload))
        return requeued
