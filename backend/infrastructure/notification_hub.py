"""In-app notifications for the web's job-events WebSocket.

Each open socket subscribes with its account and whether it is staff (owner/admin).
A notification goes either to one account (its own job was created, finished or
failed) or to every staff socket (a user reported a problem with a video). Publishing
is safe from worker threads and request threads; a slow socket drops notifications
instead of holding up the others.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class NotificationHub:
    def __init__(self, *, queue_size: int = 32) -> None:
        self._queue_size = queue_size
        self._subscribers: dict[asyncio.Queue[dict[str, Any]], tuple[str, bool]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()

    async def stop(self) -> None:
        self._loop = None
        self._subscribers.clear()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self, user_id: str, *, staff: bool) -> asyncio.Queue[dict[str, Any]]:
        """Called on the event loop by the socket that will read the queue."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers[queue] = (str(user_id), bool(staff))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.pop(queue, None)

    def publish_to_user(self, user_id: str, payload: dict[str, Any]) -> None:
        if user_id:
            self._publish(dict(payload), user_id=str(user_id), staff=False)

    def publish_to_staff(self, payload: dict[str, Any]) -> None:
        self._publish(dict(payload), user_id="", staff=True)

    def _publish(self, payload: dict[str, Any], *, user_id: str, staff: bool) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._deliver, payload, user_id, staff)
        except RuntimeError:  # loop shutting down
            pass

    def _deliver(self, payload: dict[str, Any], user_id: str, staff: bool) -> None:
        for queue, (subscriber, is_staff) in list(self._subscribers.items()):
            if (staff and is_staff) or (user_id and subscriber == user_id):
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    logger.warning("Notification dropped for a slow socket")
