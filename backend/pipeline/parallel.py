from __future__ import annotations

from concurrent.futures import Future, as_completed
from typing import TypeVar


T = TypeVar("T")


def collect_indexed_futures(futures: dict[Future[T], int]) -> dict[int, T]:
    """Collect results and propagate the original worker error with a safe index note."""

    results: dict[int, T] = {}
    try:
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except BaseException as exc:
                if hasattr(exc, "add_note"):
                    exc.add_note(f"parallel_task_index={index}")
                raise
    except BaseException:
        for future in futures:
            if not future.done():
                future.cancel()
        raise
    return results
