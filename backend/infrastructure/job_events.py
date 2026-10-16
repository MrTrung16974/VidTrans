from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Wildcard change: something touched many jobs at once (e.g. crash recovery),
# so subscribers should reload their list instead of patching it.
ALL_JOBS = "*"

SnapshotBuilder = Callable[[set[str]], dict[str, Any]]


class JobEventHub:
    """Push coalesced job changes to WebSocket subscribers.

    Pipeline stages update jobs from worker threads many times per second.
    ``notify`` only records the job id; one broadcaster task turns every burst
    into a single message per ``interval``, and builds it once no matter how
    many browser tabs are connected. Nothing is read from SQLite while nobody
    is listening.
    """

    def __init__(self, build_snapshot: SnapshotBuilder, *, interval: float = 0.5, queue_size: int = 8) -> None:
        self._build_snapshot = build_snapshot
        self._interval = interval
        self._queue_size = queue_size
        self._lock = threading.Lock()
        self._dirty: set[str] = set()
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._broadcast_loop(), name="vidtrans-job-events")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._loop = None

    def notify(self, job_id: str) -> None:
        """Record a change. Safe to call from any thread, before start() too."""
        with self._lock:
            self._dirty.add(job_id)
        loop, wake = self._loop, self._wake
        if loop is None or wake is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:  # loop shutting down
            pass

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def _take_dirty(self) -> set[str]:
        with self._lock:
            dirty, self._dirty = self._dirty, set()
        return dirty

    async def _broadcast_loop(self) -> None:
        assert self._wake is not None
        while True:
            await self._wake.wait()
            self._wake.clear()
            # Let a burst of progress updates accumulate into one message.
            await asyncio.sleep(self._interval)
            dirty = self._take_dirty()
            if not dirty or not self._subscribers:
                continue
            try:
                message = await asyncio.to_thread(self._build_snapshot, dirty)
            except Exception:
                logger.exception("Cannot build job change snapshot")
                message = {"type": "jobs", "resync": True, "items": [], "removed": []}
            self._publish(message)

    def _publish(self, message: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                # A slow client missed updates; tell it to reload once rather
                # than buffering an unbounded backlog for it.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "jobs", "resync": True, "items": [], "removed": []})
                continue
            queue.put_nowait(message)
