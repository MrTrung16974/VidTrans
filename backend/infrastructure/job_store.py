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

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return self._decode(row["payload_json"])

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
