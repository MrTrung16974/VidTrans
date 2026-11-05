"""Problem reports users send about one of their processed videos.

A report names the job, what went wrong (category) and, optionally, where in the
video and a free-text note. Rows stay until an admin resolves them; resolved rows
are kept so the history of a job's problems is not lost.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from infrastructure.database import connect, prepare_target

CATEGORIES = {
    "translation": "Dịch sai / dịch thiếu",
    "transcript": "Nhận sai lời gốc",
    "subtitle": "Phụ đề lệch giờ / sai vị trí",
    "voice": "Lồng tiếng / âm thanh lỗi",
    "video": "Video lỗi / không xem được",
    "other": "Khác",
}
STATUSES = {"new", "resolved"}
MAX_MESSAGE_CHARS = 2000


class JobReportError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobReportStore:
    def __init__(self, database_path: Any) -> None:
        self.database_path = prepare_target(database_path)
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS job_reports (
                    report_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    job_title TEXT,
                    reporter_user_id TEXT NOT NULL,
                    reporter_email TEXT,
                    category TEXT NOT NULL,
                    message TEXT,
                    video_time TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolved_by TEXT
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_job_reports_status ON job_reports(status, created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_job_reports_job ON job_reports(job_id, created_at)")

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
        item["category_label"] = CATEGORIES.get(str(item.get("category")), "Khác")
        return item

    def create(
        self, *, job_id: str, job_title: str, reporter_user_id: str, reporter_email: str,
        category: str, message: str, video_time: str = "",
    ) -> dict[str, Any]:
        message, video_time = message.strip(), video_time.strip()
        if category not in CATEGORIES:
            raise JobReportError("Loại lỗi không hợp lệ")
        if len(message) > MAX_MESSAGE_CHARS:
            raise JobReportError(f"Mô tả tối đa {MAX_MESSAGE_CHARS} ký tự")
        if category == "other" and len(message) < 5:
            raise JobReportError("Hãy mô tả lỗi bạn gặp")
        if len(video_time) > 20 or (video_time and not all(ch.isdigit() or ch in ":.- " for ch in video_time)):
            raise JobReportError("Thời điểm trong video không hợp lệ (ví dụ 1:23)")
        report_id = uuid.uuid4().hex
        with self._db() as db:
            db.execute("""
                INSERT INTO job_reports(report_id,job_id,job_title,reporter_user_id,reporter_email,category,
                                        message,video_time,status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (report_id, job_id, job_title[:300] or None, reporter_user_id, reporter_email or None,
                  category, message or None, video_time or None, "new", utc_now()))
        return self.get(report_id)

    def get(self, report_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM job_reports WHERE report_id=?", (report_id,)).fetchone()
        if row is None:
            raise JobReportError("Không tìm thấy báo lỗi")
        return self._row(row)

    def list(self, *, status: str = "", limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        if status and status not in STATUSES:
            raise JobReportError("Bộ lọc trạng thái không hợp lệ")
        clause, values = (" WHERE status=?", [status]) if status else ("", [])
        with self._db() as db:
            total = int(db.execute(f"SELECT COUNT(*) FROM job_reports{clause}", values).fetchone()[0])
            rows = db.execute(
                f"SELECT * FROM job_reports{clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*values, limit, offset],
            ).fetchall()
        return [self._row(row) for row in rows], total

    def count_new(self) -> int:
        with self._db() as db:
            return int(db.execute("SELECT COUNT(*) FROM job_reports WHERE status='new'").fetchone()[0])

    def resolve(self, report_id: str, actor_user_id: str) -> dict[str, Any]:
        """Mark handled; resolving twice keeps the first resolver. Raises when missing."""
        with self._db() as db:
            db.execute(
                "UPDATE job_reports SET status='resolved',resolved_at=?,resolved_by=? WHERE report_id=? AND status='new'",
                (utc_now(), actor_user_id, report_id),
            )
        return self.get(report_id)
