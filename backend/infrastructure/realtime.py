from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import socketio

from infrastructure.job_events import JobEventHub

logger = logging.getLogger(__name__)

SOCKET_IO_PATH = "/api/v1/socket.io"
JOBS_ALL_ROOM = "jobs:all"
# Owner and admin accounts: notifications meant for the staff (problem reports).
STAFF_ROOM = "staff"
# "created" is the owner's own new job; the others are its outcome.
# "awaiting_input" asks the owner to choose (a photo post: images or a slideshow).
NOTIFIED_STATUSES = {"created", "completed", "failed", "awaiting_input"}
RESYNC = {"type": "jobs", "resync": True, "items": [], "removed": []}

# (scope, auth payload) -> token claims, or None to refuse the connection.
Authenticate = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None]
# (message, owner_user_id, allow_all) -> the part of a change set that user may see.
ScopeMessage = Callable[[dict[str, Any], str, bool], dict[str, Any] | None]
# (job_id, status) -> (owner_user_id, notification payload), or None.
BuildNotification = Callable[[str, str], tuple[str, dict[str, Any]] | None]


def user_room(user_id: str) -> str:
    """Notifications for one account, on every device it is signed in on."""
    return f"user:{user_id}"


def user_jobs_room(user_id: str) -> str:
    """Job changes of one account; owners and admins join JOBS_ALL_ROOM instead."""
    return f"jobs:user:{user_id}"


class RealtimeServer:
    """Socket.IO endpoint for job progress and in-app notifications.

    Events sent to clients (on Socket.IO's own ``connect``, reload the job list
    once: changes may have been missed while disconnected):
    - ``jobs``: the change sets built by :class:`JobEventHub`, scoped to the account.
    - ``notification``: the account's own job was created, finished or failed
      (``type: job_status``); for owner/admin accounts also a user's problem report
      about a video (``type: job_report``).
    - ``auth_expired``: the access token expired; refresh it and connect again.

    Only the WebSocket transport is enabled: long-polling would be plain HTTP
    requests that the cookie/CSRF middleware treats like any other API call.
    """

    def __init__(self, hub: JobEventHub, *, authenticate: Authenticate, scope_message: ScopeMessage,
                 build_notification: BuildNotification, expiry_check_seconds: float = 30.0) -> None:
        self._hub = hub
        self._authenticate = authenticate
        self._scope_message = scope_message
        self._build_notification = build_notification
        self._expiry_check_seconds = expiry_check_seconds
        # Origins are checked by `authenticate` (cookie sessions must come from the
        # page's own host); native apps send no Origin at all.
        self.sio = socketio.AsyncServer(
            async_mode="asgi", cors_allowed_origins="*", transports=["websocket"],
            ping_interval=25, ping_timeout=20, logger=False, engineio_logger=False,
        )
        self.asgi_app = socketio.ASGIApp(self.sio, socketio_path=SOCKET_IO_PATH.lstrip("/"))
        self._expires_at: dict[str, float] = {}
        self._connected: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._expiry_task: asyncio.Task[None] | None = None
        # Subscribed to the hub only while someone is connected, so job changes
        # are not read back from the database for nobody.
        self._forwarder: asyncio.Task[None] | None = None
        self.sio.on("connect", self._on_connect)
        self.sio.on("disconnect", self._on_disconnect)

    async def start(self) -> None:
        if self._expiry_task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._expiry_task = asyncio.create_task(self._expire_sessions(), name="vidtrans-realtime-expiry")

    async def stop(self) -> None:
        tasks = [task for task in (self._expiry_task, self._forwarder) if task is not None]
        self._expiry_task = self._forwarder = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loop = None

    async def _on_connect(self, sid: str, environ: dict[str, Any], auth: Any = None) -> None:
        scope = environ.get("asgi.scope") or {}
        claims = await asyncio.to_thread(self._authenticate, scope, auth if isinstance(auth, dict) else {})
        if claims is None:
            raise socketio.exceptions.ConnectionRefusedError("unauthorized")
        user_id = str(claims.get("user_id") or "")
        await self.sio.enter_room(sid, user_room(user_id))
        staff = str(claims.get("role") or "") in {"owner", "admin"}
        await self.sio.enter_room(sid, JOBS_ALL_ROOM if staff else user_jobs_room(user_id))
        if staff:
            await self.sio.enter_room(sid, STAFF_ROOM)
        if claims.get("exp"):
            self._expires_at[sid] = float(claims["exp"])
        self._connected.add(sid)
        if self._forwarder is None:
            self._forwarder = asyncio.create_task(
                self._forward_job_changes(self._hub.subscribe()), name="vidtrans-realtime-jobs",
            )

    async def _on_disconnect(self, sid: str, *_reason: Any) -> None:
        self._expires_at.pop(sid, None)
        self._connected.discard(sid)
        if not self._connected and self._forwarder is not None:
            self._forwarder, forwarder = None, self._forwarder
            forwarder.cancel()

    async def _forward_job_changes(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            while True:
                message = await queue.get()
                try:
                    await self.publish_jobs(message)
                except Exception:
                    logger.exception("Cannot publish job changes")
        finally:
            self._hub.unsubscribe(queue)

    async def publish_jobs(self, message: dict[str, Any]) -> None:
        # Deletions cannot be attributed to an owner once the row is gone: everyone reloads.
        if message.get("resync") or message.get("removed"):
            await self.sio.emit("jobs", dict(RESYNC))
            return
        everything = self._scope_message(message, "", True)
        if everything is not None:
            await self.sio.emit("jobs", everything, room=JOBS_ALL_ROOM)
        owners = {str(item.get("_owner_user_id") or "") for item in message.get("items", [])}
        for owner in sorted(owners - {""}):
            scoped = self._scope_message(message, owner, False)
            if scoped is not None:
                await self.sio.emit("jobs", scoped, room=user_jobs_room(owner))

    async def _expire_sessions(self) -> None:
        while True:
            await asyncio.sleep(self._expiry_check_seconds)
            now = time.time()
            for sid, expires_at in list(self._expires_at.items()):
                if expires_at <= now:
                    self._expires_at.pop(sid, None)
                    await self.sio.emit("auth_expired", {"type": "auth_expired"}, to=sid)
                    await self.sio.disconnect(sid)

    def on_status(self, job_id: str, status: str) -> None:
        """Called from worker threads right after a job's status was written."""
        loop = self._loop
        if status not in NOTIFIED_STATUSES or loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._notify(job_id, status), loop)
        except RuntimeError:  # loop shutting down
            pass

    def notify_staff(self, payload: dict[str, Any]) -> None:
        """Send a notification to every connected owner/admin; safe from any thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._emit_staff(dict(payload)), loop)
        except RuntimeError:  # loop shutting down
            pass

    async def _emit_staff(self, payload: dict[str, Any]) -> None:
        try:
            await self.sio.emit("notification", payload, room=STAFF_ROOM)
        except Exception:
            logger.exception("Staff notification failed")

    async def _notify(self, job_id: str, status: str) -> None:
        try:
            built = await asyncio.to_thread(self._build_notification, job_id, status)
            if built is not None:
                owner, payload = built
                await self.sio.emit("notification", payload, room=user_room(owner))
        except Exception:
            logger.exception("Realtime notification for job %s failed", job_id)
