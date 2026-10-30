"""Mobile devices that receive push notifications, and the pushes already sent.

``user_devices`` holds one row per FCM token: a token moves to whoever signed in
last on that phone. ``push_deliveries`` remembers which job outcome was already
pushed so a worker restart that writes ``completed`` again stays silent; a retry
clears the job's rows so the next outcome is pushed.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from infrastructure.database import connect, prepare_target

PLATFORMS = {"android", "ios"}
# Keeps a forgotten phone list from growing without bound.
MAX_DEVICES_PER_USER = 20


class DeviceError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeviceStore:
    def __init__(self, database_path: Any) -> None:
        self.database_path = prepare_target(database_path)
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS user_devices (
                    device_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    platform TEXT NOT NULL,
                    app_version TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_user_devices_user ON user_devices(user_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS push_deliveries (
                    job_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    claim_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, status)
                )
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = connect(self.database_path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _public(row: Any) -> dict[str, Any]:
        return {
            "device_id": str(row["device_id"]),
            "platform": str(row["platform"]),
            "app_version": row["app_version"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def register(self, user_id: str, token: str, platform: str, app_version: str | None = None) -> dict[str, Any]:
        user_id = str(user_id or "").strip()
        token = str(token or "").strip()
        if not user_id or not token:
            raise DeviceError("Thiếu người dùng hoặc token thiết bị")
        if platform not in PLATFORMS:
            raise DeviceError("platform phải là android hoặc ios")
        now = utc_now()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT device_id FROM user_devices WHERE token=?", (token,)).fetchone()
            if row is None:
                device_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO user_devices(device_id, user_id, token, platform, app_version, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (device_id, user_id, token, platform, app_version, now, now),
                )
            else:
                device_id = str(row["device_id"])
                db.execute(
                    "UPDATE user_devices SET user_id=?, platform=?, app_version=?, updated_at=? WHERE device_id=?",
                    (user_id, platform, app_version, now, device_id),
                )
            stale = db.execute(
                "SELECT device_id FROM user_devices WHERE user_id=? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()[MAX_DEVICES_PER_USER:]
            for old in stale:
                db.execute("DELETE FROM user_devices WHERE device_id=?", (str(old["device_id"]),))
            saved = db.execute("SELECT * FROM user_devices WHERE device_id=?", (device_id,)).fetchone()
        return self._public(saved)

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM user_devices WHERE user_id=? ORDER BY updated_at DESC", (str(user_id),)
            ).fetchall()
        return [self._public(row) for row in rows]

    def tokens_for_user(self, user_id: str) -> list[str]:
        with self._db() as db:
            rows = db.execute("SELECT token FROM user_devices WHERE user_id=?", (str(user_id),)).fetchall()
        return [str(row["token"]) for row in rows]

    def delete(self, user_id: str, device_id: str) -> bool:
        with self._db() as db:
            row = db.execute(
                "SELECT device_id FROM user_devices WHERE device_id=? AND user_id=?", (device_id, str(user_id))
            ).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM user_devices WHERE device_id=?", (device_id,))
        return True

    def delete_token(self, token: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM user_devices WHERE token=?", (token,))

    def delete_user(self, user_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM user_devices WHERE user_id=?", (str(user_id),))

    def claim_delivery(self, job_id: str, status: str) -> bool:
        """True exactly once per (job, outcome) until ``reset_deliveries``."""
        token = uuid.uuid4().hex
        with self._db() as db:
            db.execute(
                "INSERT OR IGNORE INTO push_deliveries(job_id, status, claim_token, created_at) VALUES (?, ?, ?, ?)",
                (job_id, status, token, utc_now()),
            )
            row = db.execute(
                "SELECT claim_token FROM push_deliveries WHERE job_id=? AND status=?", (job_id, status)
            ).fetchone()
        return row is not None and str(row["claim_token"]) == token

    def reset_deliveries(self, job_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM push_deliveries WHERE job_id=?", (job_id,))
