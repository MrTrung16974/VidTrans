"""Messages sent through the public /contact form.

Rows stay until an admin resolves them; resolved rows are kept as a support history.
No IP address is stored: abuse is bounded by the in-memory rate limit in the route.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from infrastructure.database import connect, prepare_target

TOPICS = {
    "support": "Hỗ trợ kỹ thuật",
    "billing": "Thanh toán / kích hoạt gói",
    "bug": "Báo lỗi",
    "privacy": "Dữ liệu cá nhân / xoá tài khoản",
    "partnership": "Hợp tác",
    "other": "Khác",
}
STATUSES = {"new", "resolved"}


class ContactError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContactMessageStore:
    def __init__(self, database_path: Any) -> None:
        self.database_path = prepare_target(database_path)
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS contact_messages (
                    message_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    phone TEXT,
                    topic TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolved_by TEXT
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_contact_messages_status ON contact_messages(status, created_at)")

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
    def _row(row: Any) -> dict[str, Any]:
        item = {key: row[key] for key in row.keys()}
        item["topic_label"] = TOPICS.get(str(item.get("topic")), "Khác")
        return item

    def create(self, *, name: str, email: str, phone: str, topic: str, message: str) -> dict[str, Any]:
        name, email, phone, message = name.strip(), email.strip().lower(), phone.strip(), message.strip()
        if not name or len(name) > 120:
            raise ContactError("Họ tên phải từ 1 đến 120 ký tự")
        local, _, domain = email.partition("@")
        if not local or "." not in domain or len(email) > 254 or any(ch.isspace() for ch in email):
            raise ContactError("Email không hợp lệ")
        if len(phone) > 30 or (phone and not all(ch.isdigit() or ch in "+ .-()" for ch in phone)):
            raise ContactError("Số điện thoại không hợp lệ")
        if topic not in TOPICS:
            raise ContactError("Chủ đề không hợp lệ")
        if not 10 <= len(message) <= 4000:
            raise ContactError("Nội dung phải từ 10 đến 4000 ký tự")
        message_id = uuid.uuid4().hex
        with self._db() as db:
            db.execute("""
                INSERT INTO contact_messages(message_id,name,email,phone,topic,message,status,created_at)
                VALUES(?,?,?,?,?,?,?,?)
            """, (message_id, name, email, phone or None, topic, message, "new", utc_now()))
        return self.get(message_id)

    def get(self, message_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM contact_messages WHERE message_id=?", (message_id,)).fetchone()
        if row is None:
            raise ContactError("Không tìm thấy tin nhắn")
        return self._row(row)

    def list(self, *, status: str = "", limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        if status and status not in STATUSES:
            raise ContactError("Bộ lọc trạng thái không hợp lệ")
        clause, values = (" WHERE status=?", [status]) if status else ("", [])
        with self._db() as db:
            total = int(db.execute(f"SELECT COUNT(*) FROM contact_messages{clause}", values).fetchone()[0])
            rows = db.execute(
                f"SELECT * FROM contact_messages{clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*values, limit, offset],
            ).fetchall()
        return [self._row(row) for row in rows], total

    def count_new(self) -> int:
        with self._db() as db:
            return int(db.execute("SELECT COUNT(*) FROM contact_messages WHERE status='new'").fetchone()[0])

    def resolve(self, message_id: str, actor_user_id: str) -> dict[str, Any]:
        """Mark handled; resolving twice keeps the first resolver. Raises when missing."""
        with self._db() as db:
            db.execute(
                "UPDATE contact_messages SET status='resolved',resolved_at=?,resolved_by=? WHERE message_id=? AND status='new'",
                (utc_now(), actor_user_id, message_id),
            )
        return self.get(message_id)
