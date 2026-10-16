from __future__ import annotations

import unittest
from concurrent.futures import Future

from pipeline.parallel import collect_indexed_futures


class ParallelResultTests(unittest.TestCase):
    def test_returns_results_keyed_by_original_index(self) -> None:
        first: Future[str] = Future()
        second: Future[str] = Future()
        second.set_result("second")
        first.set_result("first")

        result = collect_indexed_futures({first: 4, second: 2})

        self.assertEqual(result, {4: "first", 2: "second"})

    def test_propagates_exact_worker_error_with_safe_index_note(self) -> None:
        failed: Future[str] = Future()
        pending: Future[str] = Future()
        error = RuntimeError("synthetic worker failure")
        failed.set_exception(error)

        with self.assertRaises(RuntimeError) as raised:
            collect_indexed_futures({failed: 7, pending: 8})

        self.assertIs(raised.exception, error)
        self.assertTrue(pending.cancelled())
        self.assertIn("parallel_task_index=7", getattr(error, "__notes__", []))


if __name__ == "__main__":
    unittest.main()
