"""Opt-in PostgreSQL repository smoke test.

Run with a disposable database URL:
    VIDTRANS_POSTGRES_TEST_URL=postgresql://... python tests/postgres_smoke.py
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

from infrastructure.admin_accounts import AdminAccountStore
from infrastructure.job_store import SQLiteJobStore
from infrastructure.payments import PaymentOrderStore
from infrastructure.social_credentials import SocialCredentialStore
from infrastructure.social_publish_store import SQLiteSocialPublishStore
from infrastructure.tenant_governance import TenantGovernanceStore
from infrastructure.tiktok_publish_store import SQLiteTikTokPublishStore
from infrastructure.workflow_events import WorkflowEventStore
from scripts.migrate_sqlite_to_postgres import migrate


def main() -> None:
    dsn = os.environ.get("VIDTRANS_POSTGRES_TEST_URL", "").strip()
    if not dsn:
        raise SystemExit("VIDTRANS_POSTGRES_TEST_URL is required")

    suffix = uuid.uuid4().hex[:8]
    accounts = AdminAccountStore(dsn, f"smoke-{suffix}")
    owner = accounts.get_by_email(f"smoke-{suffix}@local.vidtrans")
    assert owner is not None

    jobs = SQLiteJobStore(dsn)
    job_id = f"smoke-job-{suffix}"
    jobs.create(job_id, {"status": "queued"}, owner_user_id=str(owner["user_id"]))
    assert jobs.get(job_id)["status"] == "queued"
    jobs.update(job_id, status="completed")
    assert jobs.get(job_id)["status"] == "completed"

    events = WorkflowEventStore(dsn)
    event = events.enqueue("smoke.completed", job_id, {"ok": True})
    claimed = events.claim_next()
    assert claimed is not None and claimed["event_id"] == event["event_id"]
    events.mark_delivered(event["event_id"])

    SQLiteTikTokPublishStore(dsn)
    SQLiteSocialPublishStore(dsn)
    SocialCredentialStore(dsn)
    PaymentOrderStore(dsn)
    TenantGovernanceStore(dsn)

    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "legacy.sqlite3"
        table = f"migration_probe_{suffix}"
        legacy = sqlite3.connect(source)
        legacy.executescript(f"""
            CREATE TABLE admin_accounts (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                approval_status TEXT NOT NULL,
                plan TEXT NOT NULL,
                payment_status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE admin_audit_events (
                event_id TEXT PRIMARY KEY,
                actor_user_id TEXT NOT NULL,
                target_user_id TEXT,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{{}}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(actor_user_id) REFERENCES admin_accounts(user_id)
            );
            CREATE TABLE {table} (id TEXT PRIMARY KEY, value BLOB NOT NULL);
        """)
        legacy.execute(
            "INSERT INTO admin_accounts(user_id,email,display_name,role,approval_status,plan,payment_status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("legacy-owner", f"smoke-{suffix}@local.vidtrans", "Legacy", "owner", "approved", "enterprise", "exempt", "now", "now"),
        )
        legacy.execute(
            "INSERT INTO admin_audit_events(event_id,actor_user_id,action,resource_type,metadata_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (f"audit-{suffix}", "legacy-owner", "smoke", "test", "{}", "now"),
        )
        legacy.execute(f"INSERT INTO {table}(id,value) VALUES(?,?)", (suffix, b"ok"))
        legacy.commit()
        legacy.close()
        assert migrate(source, dsn)[table] == 1
    print("PostgreSQL repository smoke test passed")


if __name__ == "__main__":
    main()
