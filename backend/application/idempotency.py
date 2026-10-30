"""Optional ``Idempotency-Key`` header for endpoints that create jobs.

Mobile networks drop responses: the app retries with the same key, and the server
returns the first result instead of uploading, reserving quota and queueing again.
Keys are scoped per user and per endpoint and kept in the job database for a day.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from fastapi import Header, HTTPException, Request

from application.job_service import JobService

IDEMPOTENCY_TTL_SECONDS = 24 * 3600
# A request that crashed without releasing its claim stops blocking retries after this.
PENDING_TTL_SECONDS = 15 * 60


@dataclass
class IdempotentRequest:
    """Handed to the endpoint: replay ``replay`` if set, else do the work and ``complete`` it."""

    replay: dict[str, Any] | None = None
    _complete: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)
    completed: bool = False

    def complete(self, response: dict[str, Any]) -> None:
        if self._complete is not None:
            self._complete(response)
        self.completed = True


def idempotency_guard(
    job_service: Callable[[], JobService],
    current_account: Callable[[Request], dict[str, Any]],
    scope: str,
    still_valid: Callable[[dict[str, Any]], bool],
) -> Callable[..., Iterator[IdempotentRequest]]:
    """Build a dependency for one endpoint; ``still_valid`` rejects results not worth replaying.

    ``job_service`` is looked up per request so tests can swap the store.
    """

    def guard(
        request: Request,
        idempotency_key: str | None = Header(
            default=None, max_length=128,
            description="Optional. Retrying with the same key returns the first result instead of creating again.",
        ),
    ) -> Iterator[IdempotentRequest]:
        key = (idempotency_key or "").strip()
        if not key:
            yield IdempotentRequest()
            return
        owner = str(current_account(request)["user_id"])
        service = job_service()
        state, value = service.claim_idempotency_key(
            owner, scope, key, pending_ttl_seconds=PENDING_TTL_SECONDS, ttl_seconds=IDEMPOTENCY_TTL_SECONDS
        )
        if state == "pending":
            raise HTTPException(409, "Yêu cầu với Idempotency-Key này đang được xử lý, hãy thử lại sau")
        if state == "done":
            if still_valid(value):
                yield IdempotentRequest(replay={**value, "duplicate": True})
                return
            token = service.reclaim_idempotency_key(owner, scope, key)
        else:
            token = value
        handle = IdempotentRequest(
            _complete=lambda response: service.complete_idempotency_key(owner, scope, key, token, response)
        )
        try:
            yield handle
        finally:
            # Failed or rejected requests free the key so the client can retry it.
            if not handle.completed:
                service.release_idempotency_key(owner, scope, key, token)

    return guard
