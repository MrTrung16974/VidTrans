from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from application.job_service import JobService


logger = logging.getLogger(__name__)
JobRunner = Callable[[str, dict[str, Any]], None]


class JobScheduler:
    """Persistent SQLite-backed worker pool for a single VidTrans deployment."""

    def __init__(
        self,
        service: JobService,
        runner: JobRunner,
        *,
        concurrency: int = 1,
        poll_interval: float = 1.0,
    ) -> None:
        if concurrency < 1:
            raise ValueError("scheduler concurrency must be at least 1")
        self._service = service
        self._runner = runner
        self._concurrency = concurrency
        self._poll_interval = poll_interval
        self._wake_event = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = False

    @property
    def concurrency(self) -> int:
        return self._concurrency

    async def start(self) -> None:
        if self._tasks:
            return
        self._stopping = False
        self._tasks = [
            asyncio.create_task(self._worker(index + 1), name=f"vidtrans-worker-{index + 1}")
            for index in range(self._concurrency)
        ]
        self.notify()

    async def stop(self) -> None:
        self._stopping = True
        self.notify()
        tasks, self._tasks = self._tasks, []
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def notify(self) -> None:
        self._wake_event.set()

    async def _worker(self, number: int) -> None:
        worker_id = f"local-{number}"
        while not self._stopping:
            claimed = await asyncio.to_thread(self._service.claim_next, worker_id)
            if claimed is not None:
                job_id, job = claimed
                resume_request = job.get("resume_request")
                if not isinstance(resume_request, dict):
                    self._service.update(
                        job_id,
                        status="failed",
                        step="failed",
                        error="Job không có cấu hình chạy lại hợp lệ",
                    )
                    continue
                try:
                    await asyncio.to_thread(self._runner, job_id, resume_request)
                except Exception:
                    logger.exception("Unhandled worker failure for job %s", job_id)
                    self._service.update(
                        job_id,
                        status="failed",
                        step="failed",
                        error="Worker gặp lỗi ngoài pipeline",
                    )
                continue

            self._wake_event.clear()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=self._poll_interval)
            except TimeoutError:
                pass
