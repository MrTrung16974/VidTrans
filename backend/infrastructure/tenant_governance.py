from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def billing_period(value: datetime | None = None) -> str:
    moment = value or datetime.now(timezone.utc)
    return f"{moment.year:04d}-{moment.month:02d}"


class QuotaExceededError(ValueError):
    pass


class TenantGovernanceStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self._initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS quota_job_usage (
                    job_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    reserved_minutes INTEGER NOT NULL DEFAULT 0,
                    consumed_minutes INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES admin_accounts(user_id) ON DELETE CASCADE,
                    CHECK(status IN ('reserved','consumed','released'))
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_quota_user_period ON quota_job_usage(user_id,period,status)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS admin_audit_events (
                    event_id TEXT PRIMARY KEY,
                    actor_user_id TEXT NOT NULL,
                    target_user_id TEXT,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(actor_user_id) REFERENCES admin_accounts(user_id) ON DELETE CASCADE
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_events(created_at DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_actor ON admin_audit_events(actor_user_id,created_at DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_target ON admin_audit_events(target_user_id,created_at DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_action ON admin_audit_events(action,created_at DESC)")
            db.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_quota_nonnegative_insert
                BEFORE INSERT ON quota_job_usage
                FOR EACH ROW
                WHEN NEW.reserved_minutes < 0 OR NEW.consumed_minutes < 0
                BEGIN
                    SELECT RAISE(ABORT, 'quota minutes cannot be negative');
                END;
                """
            )
            db.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_quota_nonnegative_update
                BEFORE UPDATE OF reserved_minutes,consumed_minutes ON quota_job_usage
                FOR EACH ROW
                WHEN NEW.reserved_minutes < 0 OR NEW.consumed_minutes < 0
                BEGIN
                    SELECT RAISE(ABORT, 'quota minutes cannot be negative');
                END;
                """
            )
            db.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_quota_identity_immutable
                BEFORE UPDATE OF user_id,period ON quota_job_usage
                FOR EACH ROW
                WHEN NEW.user_id != OLD.user_id OR NEW.period != OLD.period
                BEGIN
                    SELECT RAISE(ABORT, 'quota identity is immutable');
                END;
                """
            )

    @staticmethod
    def minutes_for_duration(seconds: float | int | None) -> int:
        return max(1, int(math.ceil(max(0.0, float(seconds or 0)) / 60.0)))

    def reserve(self, *, job_id: str, user_id: str, limit_minutes: int | None, minutes: int = 1) -> dict[str, Any]:
        minutes = max(1, int(minutes))
        period = billing_period()
        now = utc_now()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM quota_job_usage WHERE job_id=?", (job_id,)).fetchone()
            if existing is not None and existing["status"] != "released":
                return dict(existing)
            totals = db.execute(
                "SELECT COALESCE(SUM(reserved_minutes),0),COALESCE(SUM(consumed_minutes),0) FROM quota_job_usage WHERE user_id=? AND period=? AND status!='released'",
                (user_id, period),
            ).fetchone()
            used = int(totals[0]) + int(totals[1])
            if limit_minutes is not None and used + minutes > limit_minutes:
                raise QuotaExceededError(f"Đã dùng hoặc giữ chỗ {used}/{limit_minutes} phút trong tháng này")
            if existing is not None:
                db.execute("DELETE FROM quota_job_usage WHERE job_id=? AND status='released'", (job_id,))
            db.execute(
                "INSERT INTO quota_job_usage(job_id,user_id,period,reserved_minutes,status,created_at,updated_at) VALUES(?,?,?,?, 'reserved',?,?)",
                (job_id, user_id, period, minutes, now, now),
            )
        return self.job_usage(job_id) or {}

    def reserve_many(self, *, job_ids: list[str], user_id: str, limit_minutes: int | None,
                     minutes_each: int = 1) -> None:
        unique_ids = list(dict.fromkeys(job_ids))
        if not unique_ids:
            return
        if len(unique_ids) != len(job_ids):
            raise ValueError("Job ID quota bị trùng")
        minutes_each = max(1, int(minutes_each))
        period, now = billing_period(), utc_now()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in unique_ids)
            existing = int(db.execute(
                f"SELECT COUNT(*) FROM quota_job_usage WHERE job_id IN ({placeholders})", unique_ids
            ).fetchone()[0])
            if existing:
                raise ValueError("Job đã giữ quota")
            totals = db.execute(
                "SELECT COALESCE(SUM(reserved_minutes),0),COALESCE(SUM(consumed_minutes),0) FROM quota_job_usage WHERE user_id=? AND period=? AND status!='released'",
                (user_id, period),
            ).fetchone()
            used = int(totals[0]) + int(totals[1])
            requested = minutes_each * len(unique_ids)
            if limit_minutes is not None and used + requested > limit_minutes:
                raise QuotaExceededError(
                    f"Batch cần giữ chỗ {requested} phút nhưng gói chỉ còn {max(0, limit_minutes-used)} phút"
                )
            db.executemany(
                "INSERT INTO quota_job_usage(job_id,user_id,period,reserved_minutes,status,created_at,updated_at) VALUES(?,?,?,?, 'reserved',?,?)",
                [(job_id, user_id, period, minutes_each, now, now) for job_id in unique_ids],
            )

    def adjust(self, *, job_id: str, limit_minutes: int | None, minutes: int) -> dict[str, Any]:
        minutes = max(1, int(minutes))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM quota_job_usage WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["status"] != "reserved":
                return dict(row) if row else {}
            totals = db.execute(
                "SELECT COALESCE(SUM(reserved_minutes),0),COALESCE(SUM(consumed_minutes),0) FROM quota_job_usage WHERE user_id=? AND period=? AND status!='released' AND job_id!=?",
                (row["user_id"], row["period"], job_id),
            ).fetchone()
            used = int(totals[0]) + int(totals[1])
            if limit_minutes is not None and used + minutes > limit_minutes:
                raise QuotaExceededError(f"Video cần {minutes} phút nhưng gói chỉ còn {max(0, limit_minutes-used)} phút")
            db.execute("UPDATE quota_job_usage SET reserved_minutes=?,updated_at=? WHERE job_id=?", (minutes, utc_now(), job_id))
        return self.job_usage(job_id) or {}

    def consume(self, job_id: str) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE quota_job_usage SET consumed_minutes=reserved_minutes,reserved_minutes=0,status='consumed',updated_at=? WHERE job_id=? AND status='reserved'",
                (utc_now(), job_id),
            )

    def release(self, job_id: str) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE quota_job_usage SET reserved_minutes=0,status='released',updated_at=? WHERE job_id=? AND status='reserved'",
                (utc_now(), job_id),
            )

    def job_usage(self, job_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM quota_job_usage WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def overview(self, user_id: str, limit_minutes: int | None) -> dict[str, Any]:
        period = billing_period()
        with self._db() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(reserved_minutes),0) reserved,COALESCE(SUM(consumed_minutes),0) consumed FROM quota_job_usage WHERE user_id=? AND period=? AND status!='released'",
                (user_id, period),
            ).fetchone()
        reserved, consumed = int(row["reserved"]), int(row["consumed"])
        return {"period": period, "limit_minutes": limit_minutes, "reserved_minutes": reserved,
                "consumed_minutes": consumed, "remaining_minutes": None if limit_minutes is None else max(0, limit_minutes-reserved-consumed)}

    def audit(self, *, actor_user_id: str, action: str, resource_type: str,
              target_user_id: str | None = None, resource_id: str | None = None,
              metadata: Mapping[str, Any] | None = None) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO admin_audit_events(event_id,actor_user_id,target_user_id,action,resource_type,resource_id,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, actor_user_id, target_user_id, action, resource_type, resource_id,
                 json.dumps(dict(metadata or {}), ensure_ascii=False, separators=(",", ":")), utc_now()),
            )

    def list_audit(self, *, limit: int = 100, offset: int = 0, actor_user_id: str | None = None,
                   target_user_id: str | None = None, action: str | None = None) -> tuple[list[dict[str, Any]], int]:
        clauses, values = [], []
        for column, value in (("actor_user_id", actor_user_id), ("target_user_id", target_user_id), ("action", action)):
            if value:
                clauses.append(f"{column}=?")
                values.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._db() as db:
            total = int(db.execute(f"SELECT COUNT(*) FROM admin_audit_events{where}", values).fetchone()[0])
            rows = db.execute(
                f"SELECT e.*,a.email actor_email,t.email target_email FROM admin_audit_events e LEFT JOIN admin_accounts a ON a.user_id=e.actor_user_id LEFT JOIN admin_accounts t ON t.user_id=e.target_user_id{where} ORDER BY e.created_at DESC LIMIT ? OFFSET ?",
                (*values, max(1, min(500, limit)), max(0, offset)),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            items.append(item)
        return items, total

    def prune(self, *, audit_cutoff: str, released_quota_cutoff: str) -> dict[str, int]:
        """Apply time-based retention without removing billable consumed usage."""
        with self._db() as db:
            audit_deleted = db.execute(
                "DELETE FROM admin_audit_events WHERE created_at < ?", (audit_cutoff,)
            ).rowcount
            quota_deleted = db.execute(
                "DELETE FROM quota_job_usage WHERE status='released' AND updated_at < ?",
                (released_quota_cutoff,),
            ).rowcount
        return {"audit_events": int(audit_deleted), "released_quota_rows": int(quota_deleted)}
