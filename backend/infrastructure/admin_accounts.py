from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ROLES = {"owner", "admin", "editor", "viewer"}
APPROVAL_STATUSES = {"pending", "approved", "suspended", "rejected"}
PAYMENT_STATUSES = {"trial", "active", "past_due", "cancelled", "exempt"}
PLANS = {"free", "creator", "pro", "enterprise"}


class AdminAccountError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AdminAccountStore:
    """Small durable directory for back-office access and billing state.

    Payment fields describe provider state only. Card data and provider secrets
    never enter this store.
    """

    def __init__(self, database_path: Path, bootstrap_username: str = "") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if bootstrap_username.strip():
            self.ensure_owner(bootstrap_username.strip())

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
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
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS admin_accounts (
                    user_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    plan TEXT NOT NULL,
                    payment_status TEXT NOT NULL,
                    provider_customer_ref TEXT,
                    approved_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_admin_accounts_status ON admin_accounts(approval_status, payment_status)")

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in row.keys()}

    @staticmethod
    def _validate(email: str, display_name: str, role: str, approval_status: str,
                  plan: str, payment_status: str) -> tuple[str, str]:
        email = email.strip().lower()
        display_name = display_name.strip()
        if not email or len(email) > 254 or "@" not in email or email.startswith("@") or email.endswith("@"):
            raise AdminAccountError("Email không hợp lệ")
        if not display_name or len(display_name) > 120:
            raise AdminAccountError("Tên hiển thị phải từ 1 đến 120 ký tự")
        if role not in ROLES:
            raise AdminAccountError("Vai trò không hợp lệ")
        if approval_status not in APPROVAL_STATUSES:
            raise AdminAccountError("Trạng thái phê duyệt không hợp lệ")
        if plan not in PLANS:
            raise AdminAccountError("Gói dịch vụ không hợp lệ")
        if payment_status not in PAYMENT_STATUSES:
            raise AdminAccountError("Trạng thái thanh toán không hợp lệ")
        return email, display_name

    def ensure_owner(self, username: str) -> None:
        email = username if "@" in username else f"{username}@local.vidtrans"
        now = utc_now()
        with self._db() as db:
            db.execute("""
                INSERT OR IGNORE INTO admin_accounts(
                    user_id,email,display_name,role,approval_status,plan,payment_status,
                    provider_customer_ref,approved_at,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (uuid.uuid4().hex, email.lower(), username, "owner", "approved", "enterprise",
                  "exempt", None, now, now, now))

    def create(self, *, email: str, display_name: str, role: str = "viewer",
               approval_status: str = "pending", plan: str = "free",
               payment_status: str = "trial") -> dict[str, Any]:
        email, display_name = self._validate(email, display_name, role, approval_status, plan, payment_status)
        now = utc_now()
        user_id = uuid.uuid4().hex
        try:
            with self._db() as db:
                db.execute("""
                    INSERT INTO admin_accounts(
                        user_id,email,display_name,role,approval_status,plan,payment_status,
                        approved_at,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """, (user_id, email, display_name, role, approval_status, plan, payment_status,
                      now if approval_status == "approved" else None, now, now))
        except sqlite3.IntegrityError as exc:
            raise AdminAccountError("Email đã tồn tại") from exc
        return self.get(user_id)

    def get(self, user_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM admin_accounts WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            raise AdminAccountError("Không tìm thấy người dùng")
        return self._row(row)

    def list(self, *, query: str = "", status: str = "") -> list[dict[str, Any]]:
        where, values = [], []
        if query.strip():
            where.append("(email LIKE ? OR display_name LIKE ?)")
            value = f"%{query.strip()}%"
            values.extend([value, value])
        if status:
            if status not in APPROVAL_STATUSES:
                raise AdminAccountError("Bộ lọc trạng thái không hợp lệ")
            where.append("approval_status=?")
            values.append(status)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._db() as db:
            rows = db.execute(
                f"SELECT * FROM admin_accounts{clause} ORDER BY created_at DESC LIMIT 500", values
            ).fetchall()
        return [self._row(row) for row in rows]

    def update(self, user_id: str, **changes: Any) -> dict[str, Any]:
        current = self.get(user_id)
        allowed = {"display_name", "role", "approval_status", "plan", "payment_status", "provider_customer_ref"}
        unknown = set(changes) - allowed
        if unknown:
            raise AdminAccountError("Trường cập nhật không hợp lệ")
        merged = {**current, **changes}
        _, display_name = self._validate(
            current["email"], str(merged["display_name"]), str(merged["role"]),
            str(merged["approval_status"]), str(merged["plan"]), str(merged["payment_status"]),
        )
        if current["role"] == "owner":
            if changes.get("role") not in {None, "owner"}:
                raise AdminAccountError("Không thể hạ quyền owner bootstrap")
            if changes.get("approval_status") not in {None, "approved"}:
                raise AdminAccountError("Không thể khóa owner bootstrap")
        provider_ref = merged.get("provider_customer_ref")
        if provider_ref is not None and len(str(provider_ref)) > 200:
            raise AdminAccountError("Mã khách hàng thanh toán quá dài")
        now = utc_now()
        approved_at = current.get("approved_at")
        if merged["approval_status"] == "approved" and not approved_at:
            approved_at = now
        with self._db() as db:
            db.execute("""
                UPDATE admin_accounts SET display_name=?,role=?,approval_status=?,plan=?,payment_status=?,
                    provider_customer_ref=?,approved_at=?,updated_at=? WHERE user_id=?
            """, (display_name, merged["role"], merged["approval_status"], merged["plan"],
                  merged["payment_status"], provider_ref, approved_at, now, user_id))
        return self.get(user_id)

    def summary(self) -> dict[str, Any]:
        with self._db() as db:
            total = int(db.execute("SELECT COUNT(*) FROM admin_accounts").fetchone()[0])
            approved = int(db.execute("SELECT COUNT(*) FROM admin_accounts WHERE approval_status='approved'").fetchone()[0])
            pending = int(db.execute("SELECT COUNT(*) FROM admin_accounts WHERE approval_status='pending'").fetchone()[0])
            payment_attention = int(db.execute(
                "SELECT COUNT(*) FROM admin_accounts WHERE payment_status IN ('past_due','cancelled')"
            ).fetchone()[0])
        return {"total": total, "approved": approved, "pending": pending, "payment_attention": payment_attention}
