from __future__ import annotations

from collections.abc import Callable
from typing import Any, Mapping

from infrastructure.job_store import JobStore


class JobService:
    """Application boundary for job lifecycle operations.

    Routes and pipeline stages use this service rather than accessing the
    persistence implementation directly.  A queue-backed implementation can
    preserve this interface when processing moves out of the API process.
    """

    def __init__(self, store: JobStore, *, on_change: Callable[[str], None] | None = None) -> None:
        self._store = store
        self._on_change = on_change

    def _changed(self, job_id: str) -> None:
        # Push notifications are best effort; a listener failure must never
        # fail the write that already committed.
        if self._on_change is None:
            return
        try:
            self._on_change(job_id)
        except Exception:
            pass

    def create(self, job_id: str, payload: Mapping[str, Any], *, owner_user_id: str = "__legacy__") -> dict[str, Any]:
        created = self._store.create(job_id, payload, owner_user_id=owner_user_id)
        self._changed(job_id)
        return created

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._store.get(job_id)

    def get_for_user(self, job_id: str, owner_user_id: str) -> dict[str, Any] | None:
        return self._store.get_for_user(job_id, owner_user_id)

    def owns_artifact(self, owner_user_id: str, filename: str) -> bool:
        return self._store.owns_artifact(owner_user_id, filename)

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        updated = self._store.update(job_id, **fields)
        self._changed(job_id)
        return updated

    def create_batch(
        self,
        batch_id: str,
        *,
        name: str,
        config: Mapping[str, Any],
        status: str = "queued",
        owner_user_id: str = "__legacy__",
    ) -> dict[str, Any]:
        return self._store.create_batch(batch_id, name=name, config=config, status=status, owner_user_id=owner_user_id)

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        return self._store.get_batch(batch_id)

    def get_batch_for_user(self, batch_id: str, owner_user_id: str) -> dict[str, Any] | None:
        return self._store.get_batch_for_user(batch_id, owner_user_id)

    def list_jobs(self, **filters: Any) -> tuple[list[dict[str, Any]], int]:
        return self._store.list_jobs(**filters)

    def status_counts(self, **filters: Any) -> dict[str, int]:
        return self._store.status_counts(**filters)

    def list_batches(self, **filters: Any) -> tuple[list[dict[str, Any]], int]:
        return self._store.list_batches(**filters)

    def attach_to_batch(self, job_id: str, batch_id: str, *, priority: int = 0) -> None:
        self._store.attach_to_batch(job_id, batch_id, priority=priority)
        self._changed(job_id)

    def claim_next(self, worker_id: str) -> tuple[str, dict[str, Any]] | None:
        claimed = self._store.claim_next(worker_id)
        if claimed is not None:
            self._changed(claimed[0])
        return claimed

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        cancelled = self._store.request_cancel(job_id)
        self._changed(job_id)
        return cancelled

    def is_cancel_requested(self, job_id: str) -> bool:
        return self._store.is_cancel_requested(job_id)

    def queue_positions(self) -> dict[str, int]:
        return self._store.queue_positions()

    def delete(self, job_id: str, *, require_terminal: bool = False) -> dict[str, Any]:
        deleted = self._store.delete(job_id, require_terminal=require_terminal)
        self._changed(job_id)
        return deleted

    def retry(self, job_id: str, **fields: Any) -> dict[str, Any]:
        retried = self._store.retry(job_id, **fields)
        self._changed(job_id)
        return retried

    def finalize_cancellation(self, job_id: str, **fields: Any) -> tuple[dict[str, Any], bool]:
        finalized, changed = self._store.finalize_cancellation(job_id, **fields)
        if changed:
            self._changed(job_id)
        return finalized, changed

    def prune_terminal(self, cutoff: str, **scope: Any) -> dict[str, Any]:
        removed = self._store.prune_terminal(cutoff, **scope)
        if removed["jobs"]:
            self._changed("*")
        return removed

    def recover_interrupted(self) -> int:
        recovered = self._store.recover_interrupted()
        if recovered:
            self._changed("*")
        return recovered

    def requeue_interrupted(
        self, max_restarts: int | None = None
    ) -> list[tuple[str, dict[str, Any]]]:
        requeued = self._store.requeue_interrupted(max_restarts)
        for job_id, _job in requeued:
            self._changed(job_id)
        return requeued

    def backfill_legacy_owner(self, owner_user_id: str) -> None:
        self._store.backfill_legacy_owner(owner_user_id)
