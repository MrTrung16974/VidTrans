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
                "priority": "INTEGER NOT NULL DEFAULT 0",
                "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
                "worker_id": "TEXT",
            }.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, priority DESC, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_batch_updated ON jobs(batch_id, updated_at DESC)"
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

    def create(self, job_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized.setdefault("status", "queued")
        now = _utc_now()
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO jobs(job_id, status, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        str(normalized["status"]),
                        self._encode(normalized),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise JobAlreadyExistsError(f"Job already exists: {job_id}") from exc
        return dict(normalized)

    def create_batch(
        self,
        batch_id: str,
        *,
        name: str,
        config: Mapping[str, Any],
        status: str = "queued",
    ) -> dict[str, Any]:
        now = _utc_now()
        payload = dict(config)
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO batches(batch_id, name, status, config_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (batch_id, name, status, self._encode(payload), now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise JobAlreadyExistsError(f"Batch already exists: {batch_id}") from exc
        return {
            "batch_id": batch_id,
            "name": name,
            "status": status,
            "config": payload,
            "created_at": now,
            "updated_at": now,
        }

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return self._decode(row["payload_json"])

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        if row is None:
            return None
        return self._batch_row(row)

    def list_jobs(
        self,
        *,
        status: str | None = None,
        batch_id: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        values: list[Any] = []
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

    def list_batches(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        with self._connection() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0])
            rows = connection.execute(
                "SELECT * FROM batches ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (max(1, min(limit, 200)), max(0, offset)),
            ).fetchall()
            batches = [self._batch_row(row) for row in rows]
            for batch in batches:
                counts = connection.execute(
                    "SELECT status, COUNT(*) count FROM jobs WHERE batch_id = ? GROUP BY status",
                    (batch["batch_id"],),
                ).fetchall()
                batch["counts"] = {str(row["status"]): int(row["count"]) for row in counts}
                batch["total_jobs"] = sum(batch["counts"].values())
                active = sum(batch["counts"].get(status, 0) for status in ("queued", "processing", "cancelling"))
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
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, payload_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, self._encode(payload), now, job_id),
            )
        return payload

    def attach_to_batch(self, job_id: str, batch_id: str, *, priority: int = 0) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(f"Job not found: {job_id}")
            payload = self._decode(row["payload_json"])
            payload["batch_id"] = batch_id
            connection.execute(
                "UPDATE jobs SET batch_id = ?, priority = ?, payload_json = ? WHERE job_id = ?",
                (batch_id, priority, self._encode(payload), job_id),
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
                WHERE status = 'queued' AND cancel_requested = 0
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            payload = self._decode(row["payload_json"])
            payload.update(
                {
                    "status": "processing",
                    "step": payload.get("step") if payload.get("step") != "queued" else "starting",
                    "worker_id": worker_id,
                    "started_at": payload.get("started_at") or now,
                    "cancel_requested": False,
                }
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = 'processing', payload_json = ?, worker_id = ?, updated_at = ?
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
            if status in {"queued", "ready"}:
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
