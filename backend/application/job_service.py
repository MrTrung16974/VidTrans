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

    def recover_interrupted(self) -> int:
        return self._store.recover_interrupted()

    def requeue_interrupted(self) -> list[tuple[str, dict[str, Any]]]:
        return self._store.requeue_interrupted()
