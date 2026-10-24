from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

SnapshotBuilder = Callable[[], dict[str, Any]]


class SystemMetricsHub:
    """Push server resource snapshots to admin WebSocket subscribers.

    Host metrics change continuously rather than on events, so one collector
    task samples them every ``interval`` and shares the result with every open
    tab. It only runs while somebody is subscribed, and a new subscriber gets a
    snapshot right away instead of waiting for the next tick.
    """

    def __init__(self, build_snapshot: SnapshotBuilder, *, interval: float = 5.0) -> None:
        self._build_snapshot = build_snapshot
        self._interval = interval
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._collect_loop(), name="vidtrans-system-metrics")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        if self._wake is not None:
            self._wake.set()
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def _collect_loop(self) -> None:
        assert self._wake is not None
        while True:
            if not self._subscribers:
                self._wake.clear()
                await self._wake.wait()
            self._wake.clear()
            try:
                message = await asyncio.to_thread(self._build_snapshot)
            except Exception:
                logger.exception("Cannot collect system metrics snapshot")
                message = {"type": "system", "error": "Không tải được số liệu"}
            self._publish(message)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    def _publish(self, message: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            # Only the latest sample matters; drop one a slow client never read.
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)
