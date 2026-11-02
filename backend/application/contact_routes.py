"""Public contact form and its admin inbox.

    POST /api/v1/contact                                  public, rate-limited, honeypot
    GET  /api/v1/admin/contact-messages?status=new|resolved
    POST /api/v1/admin/contact-messages/{id}/resolve      audited

The message is stored first; the e-mail to the support inbox is best effort and runs
after the response so SMTP latency or failure never loses a message.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from infrastructure.contact_store import ContactError, ContactMessageStore
from infrastructure.mailer import MailDeliveryError

logger = logging.getLogger(__name__)
NO_STORE = {"Cache-Control": "no-store"}
CONTACT_LIMIT_PER_CLIENT = 5
CONTACT_WINDOW_SECONDS = 60 * 60
ACCEPTED_MESSAGE = "Đã gửi tin nhắn. Chúng tôi sẽ phản hồi qua email trong vòng 24 giờ làm việc."


@dataclass
class ContactRouteDependencies:
    store: ContactMessageStore
    mailer: Any
    support_email: Callable[[], str]
    client_id: Callable[[Request], str]
    require_admin: Callable[[Request], dict[str, Any]]
    audit: Callable[..., Any]
    clock: Callable[[], float] = time.monotonic
    _recent: dict[str, list[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check_rate_limit(self, client: str) -> None:
        now = self.clock()
        with self._lock:
            recent = [stamp for stamp in self._recent.get(client, []) if stamp > now - CONTACT_WINDOW_SECONDS]
            if len(recent) >= CONTACT_LIMIT_PER_CLIENT:
                self._recent[client] = recent
                retry_after = max(1, int(CONTACT_WINDOW_SECONDS - (now - recent[0])))
                raise HTTPException(
                    status_code=429,
                    detail="Bạn đã gửi quá nhiều tin nhắn. Hãy thử lại sau hoặc gửi email trực tiếp.",
                    headers={"Retry-After": str(retry_after)},
                )
            recent.append(now)
            self._recent[client] = recent


def notify_support(deps: ContactRouteDependencies, item: dict[str, Any]) -> None:
    if not deps.mailer.configured:
        return
    try:
        deps.mailer.send(
            to=deps.support_email(),
            reply_to=item["email"],
            subject=f"[VidTrans liên hệ] {item['topic_label']} — {item['name']}",
            text=(
                f"Họ tên: {item['name']}\n"
                f"Email: {item['email']}\n"
                f"Điện thoại: {item.get('phone') or '—'}\n"
                f"Chủ đề: {item['topic_label']}\n"
                f"Thời gian: {item['created_at']}\n\n"
                f"{item['message']}\n\n"
                "— Trả lời email này để phản hồi trực tiếp cho khách. "
                "Đánh dấu đã xử lý trong Quản trị › Liên hệ.\n"
            ),
        )
    except MailDeliveryError:
        logger.exception("Contact notification e-mail delivery failed")


def create_contact_router(deps: ContactRouteDependencies) -> APIRouter:
    router = APIRouter(tags=["contact"])

    @router.post("/api/v1/contact", status_code=202)
    def submit_contact_message(
        request: Request,
        background_tasks: BackgroundTasks,
        name: str = Form(..., max_length=120),
        email: str = Form(..., max_length=254),
        topic: str = Form("support", max_length=32),
        message: str = Form(..., max_length=4000),
        phone: str = Form("", max_length=30),
        website: str = Form("", max_length=200),
    ) -> JSONResponse:
        deps.check_rate_limit(deps.client_id(request))
        # Honeypot: the field is hidden from people; a bot that fills it gets the same answer.
        if website.strip():
            return JSONResponse({"message": ACCEPTED_MESSAGE}, status_code=202, headers=NO_STORE)
        try:
            item = deps.store.create(name=name, email=email, phone=phone, topic=topic, message=message)
        except ContactError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        background_tasks.add_task(notify_support, deps, item)
        return JSONResponse({"message": ACCEPTED_MESSAGE}, status_code=202, headers=NO_STORE)

    @router.get("/api/v1/admin/contact-messages")
    def list_contact_messages(
        request: Request,
        status: str = Query(default="new", max_length=16),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        deps.require_admin(request)
        try:
            items, total = deps.store.list(status=status, limit=limit, offset=offset)
        except ContactError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse(
            {"items": items, "total": total, "new_count": deps.store.count_new(), "limit": limit, "offset": offset},
            headers=NO_STORE,
        )

    @router.post("/api/v1/admin/contact-messages/{message_id}/resolve")
    def resolve_contact_message(request: Request, message_id: str) -> JSONResponse:
        actor = deps.require_admin(request)
        try:
            item = deps.store.resolve(message_id, str(actor["user_id"]))
        except ContactError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        deps.audit(
            actor_user_id=actor["user_id"],
            action="admin.contact_message.resolve",
            resource_type="contact_message",
            resource_id=message_id,
            metadata={"topic": item["topic"]},
        )
        return JSONResponse(item, headers=NO_STORE)

    return router
