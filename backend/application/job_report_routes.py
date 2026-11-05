"""Problem reports about a processed video, and the admin inbox that handles them.

    POST /api/v1/jobs/{job_id}/reports                 the job's owner (or staff), rate-limited
    GET  /api/v1/admin/job-reports?status=new|resolved
    POST /api/v1/admin/job-reports/{report_id}/resolve audited

A new report is stored first, then every connected owner/admin account is notified
in-app; a notification that cannot be delivered never loses the report.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from infrastructure.job_report_store import CATEGORIES, JobReportError, JobReportStore

logger = logging.getLogger(__name__)
NO_STORE = {"Cache-Control": "no-store"}
REPORT_LIMIT_PER_ACCOUNT = 10
REPORT_WINDOW_SECONDS = 60 * 60
ACCEPTED_MESSAGE = "Đã gửi báo lỗi. Quản trị viên sẽ kiểm tra video này sớm."


class JobReportRequest(BaseModel):
    category: str = Field(..., max_length=32)
    message: str = Field("", max_length=2000)
    video_time: str = Field("", max_length=20)


@dataclass
class JobReportRouteDependencies:
    store: JobReportStore
    job_for_request: Callable[[Request, str], dict[str, Any] | None]
    current_account: Callable[[Request], dict[str, Any]]
    require_admin: Callable[[Request], dict[str, Any]]
    audit: Callable[..., Any]
    notify_staff: Callable[[dict[str, Any]], None]
    clock: Callable[[], float] = time.monotonic
    _recent: dict[str, list[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check_rate_limit(self, account_id: str) -> None:
        now = self.clock()
        with self._lock:
            recent = [stamp for stamp in self._recent.get(account_id, []) if stamp > now - REPORT_WINDOW_SECONDS]
            if len(recent) >= REPORT_LIMIT_PER_ACCOUNT:
                self._recent[account_id] = recent
                retry_after = max(1, int(REPORT_WINDOW_SECONDS - (now - recent[0])))
                raise HTTPException(
                    status_code=429,
                    detail="Bạn đã gửi quá nhiều báo lỗi. Hãy thử lại sau.",
                    headers={"Retry-After": str(retry_after)},
                )
            recent.append(now)
            self._recent[account_id] = recent


def job_title(job: dict[str, Any]) -> str:
    return str(job.get("filename") or job.get("title") or job.get("source_url") or job.get("job_id") or "")


def report_notification(item: dict[str, Any]) -> dict[str, Any]:
    reporter = item.get("reporter_email") or item.get("reporter_user_id") or "Người dùng"
    name = item.get("job_title") or item.get("job_id")
    detail = f" ({item['video_time']})" if item.get("video_time") else ""
    return {
        "type": "job_report",
        "report_id": item["report_id"],
        "job_id": item["job_id"],
        "category": item["category"],
        "title": "Có báo lỗi video mới",
        "body": f"{reporter} báo \"{item['category_label']}\" ở {name}{detail}.",
    }


def create_job_report_router(deps: JobReportRouteDependencies) -> APIRouter:
    router = APIRouter(tags=["job-reports"])

    @router.get("/api/v1/job-report-categories")
    def list_job_report_categories() -> JSONResponse:
        return JSONResponse(
            {"items": [{"key": key, "label": label} for key, label in CATEGORIES.items()]},
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @router.post("/api/v1/jobs/{job_id}/reports", status_code=201)
    def submit_job_report(request: Request, job_id: str, body: JobReportRequest) -> JSONResponse:
        account = deps.current_account(request)
        job = deps.job_for_request(request, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy job")
        deps.check_rate_limit(str(account["user_id"]))
        try:
            item = deps.store.create(
                job_id=job_id,
                job_title=job_title(job),
                reporter_user_id=str(account["user_id"]),
                reporter_email=str(account.get("email") or ""),
                category=body.category,
                message=body.message,
                video_time=body.video_time,
            )
        except JobReportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            deps.notify_staff(report_notification(item))
        except Exception:
            logger.exception("Staff notification for job report %s failed", item["report_id"])
        return JSONResponse(
            {"message": ACCEPTED_MESSAGE, "report_id": item["report_id"]}, status_code=201, headers=NO_STORE,
        )

    @router.get("/api/v1/admin/job-reports")
    def list_job_reports(
        request: Request,
        status: str = Query(default="new", max_length=16),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        deps.require_admin(request)
        try:
            items, total = deps.store.list(status=status, limit=limit, offset=offset)
        except JobReportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse(
            {"items": items, "total": total, "new_count": deps.store.count_new(), "limit": limit, "offset": offset},
            headers=NO_STORE,
        )

    @router.post("/api/v1/admin/job-reports/{report_id}/resolve")
    def resolve_job_report(request: Request, report_id: str) -> JSONResponse:
        actor = deps.require_admin(request)
        try:
            item = deps.store.resolve(report_id, str(actor["user_id"]))
        except JobReportError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        deps.audit(
            actor_user_id=actor["user_id"],
            action="admin.job_report.resolve",
            resource_type="job_report",
            resource_id=report_id,
            metadata={"job_id": item["job_id"], "category": item["category"]},
        )
        return JSONResponse(item, headers=NO_STORE)

    return router
