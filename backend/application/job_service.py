from __future__ import annotations

from typing import Any, Mapping

from infrastructure.job_store import SQLiteJobStore


class JobService:
    """Application boundary for job lifecycle operations.

    Routes and pipeline stages use this service rather than accessing the
    persistence implementation directly.  A queue-backed implementation can
    preserve this interface when processing moves out of the API process.
    """

    def __init__(self, store: SQLiteJobStore) -> None:
        self._store = store

    def create(self, job_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._store.create(job_id, payload)

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._store.get(job_id)

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        return self._store.update(job_id, **fields)

    def create_batch(
        self,
        batch_id: str,
        *,
        name: str,
        config: Mapping[str, Any],
        status: str = "queued",
    ) -> dict[str, Any]:
        return self._store.create_batch(batch_id, name=name, config=config, status=status)

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        return self._store.get_batch(batch_id)

    def list_jobs(self, **filters: Any) -> tuple[list[dict[str, Any]], int]:
        return self._store.list_jobs(**filters)

    def list_batches(self, **filters: Any) -> tuple[list[dict[str, Any]], int]:
        return self._store.list_batches(**filters)

    def attach_to_batch(self, job_id: str, batch_id: str, *, priority: int = 0) -> None:
        self._store.attach_to_batch(job_id, batch_id, priority=priority)

    def claim_next(self, worker_id: str) -> tuple[str, dict[str, Any]] | None:
        return self._store.claim_next(worker_id)

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        return self._store.request_cancel(job_id)

    def is_cancel_requested(self, job_id: str) -> bool:
        return self._store.is_cancel_requested(job_id)

    def queue_positions(self) -> dict[str, int]:
        return self._store.queue_positions()

    def delete(self, job_id: str) -> dict[str, Any]:
        return self._store.delete(job_id)

    def recover_interrupted(self) -> int:
        return self._store.recover_interrupted()

    def requeue_interrupted(self) -> list[tuple[str, dict[str, Any]]]:
        return self._store.requeue_interrupted()
