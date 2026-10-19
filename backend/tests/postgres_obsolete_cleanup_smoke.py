"""Destructive migration smoke test; run only against a disposable PostgreSQL database."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from infrastructure.database import connect
from infrastructure.job_store import JobStore
from infrastructure.social_publish_store import SocialPublishStore


DSN = os.environ["VIDTRANS_POSTGRES_TEST_URL"]
ROOT = Path(__file__).resolve().parents[2]
SQL_ROOT = ROOT / "DEPLOY" / "obsolete-feature-cleanup" / "sql"


def prepare() -> None:
    jobs = JobStore(DSN)
    SocialPublishStore(DSN)
    for suffix in ("terminal", "scheduled", "remote", "unknown"):
        jobs.create(f"job-{suffix}", {"status": "completed"}, owner_user_id="owner-a")
    payload = json.dumps({
        "caption": "fixture", "video_path": "/tmp/video.mp4",
        "account_id": "creator-a", "connection_id": "connection-a",
        "status_detail": {"status": "fixture"},
    })
    db = connect(DSN)
    try:
        db.execute("DROP TABLE IF EXISTS tiktok_publish_attempts_backup_20261019")
        db.execute("DROP TABLE IF EXISTS tiktok_publish_attempts")
        db.execute("""
            CREATE TABLE tiktok_publish_attempts (
                attempt_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                job_id TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
                scheduled_for TEXT, payload_json TEXT NOT NULL, remote_publish_id TEXT,
                claimed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        rows = [
            ("terminal", "key-terminal", "job-terminal", "DIRECT_NOW", "PUBLISH_COMPLETE", None, payload, "published-id", None, "2026-01-01", "2026-01-01"),
            ("scheduled", "key-scheduled", "job-scheduled", "SCHEDULE", "SCHEDULED_LOCAL", "2099-01-01T00:00:00+00:00", payload, None, None, "2026-01-01", "2026-01-01"),
            ("remote", "key-remote", "job-remote", "DIRECT_NOW", "UPLOADING", None, payload, "remote-id", None, "2026-01-01", "2026-01-01"),
            ("unknown", "key-unknown", "job-unknown", "DIRECT_NOW", "UPLOADING", None, payload, None, None, "2026-01-01", "2026-01-01"),
        ]
        db.executemany("INSERT INTO tiktok_publish_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
    finally:
        db.close()


def execute_file(name: str) -> None:
    import psycopg

    with psycopg.connect(DSN) as db:
        db.execute((SQL_ROOT / name).read_text(encoding="utf-8"))


def check_dropped() -> None:
    db = connect(DSN)
    try:
        assert db.execute("SELECT to_regclass('tiktok_publish_attempts')").fetchone()[0] is None
        assert db.execute("SELECT COUNT(*) FROM tiktok_publish_attempts_backup_20261019").fetchone()[0] == 4
        rows = db.execute(
            "SELECT status,COUNT(*) FROM social_publish_attempts "
            "WHERE attempt_id LIKE ? GROUP BY status ORDER BY status",
            ("legacy-tiktok-%",),
        ).fetchall()
        assert {str(row[0]): int(row[1]) for row in rows} == {
            "FAILED": 1, "NEEDS_RECONCILIATION": 1, "PUBLISHED": 1, "SCHEDULED_LOCAL": 1,
        }
    finally:
        db.close()


def check_rollback() -> None:
    db = connect(DSN)
    try:
        assert db.execute("SELECT COUNT(*) FROM tiktok_publish_attempts").fetchone()[0] == 4
    finally:
        db.close()


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "prepare": prepare()
    elif action == "migrate": execute_file("01_migrate_tiktok_attempts.sql")
    elif action == "drop": execute_file("02_backup_and_drop.sql")
    elif action == "check": check_dropped()
    elif action == "rollback": execute_file("rollback.sql"); check_rollback()
    else: raise SystemExit(f"Unknown action: {action}")
