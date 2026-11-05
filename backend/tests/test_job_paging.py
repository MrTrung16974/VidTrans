"""Paging the job list: every job on exactly one page, even when created together."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.job_store import JOB_PAGE_MAX, SQLiteJobStore


class JobPagingTests(unittest.TestCase):
    def test_jobs_created_in_the_same_instant_page_without_gaps_or_repeats(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteJobStore(Path(temp) / "jobs.sqlite3")
            # A batch: every job gets the same created_at.
            with patch("infrastructure.job_store._utc_now", return_value="2026-11-05T00:00:00+00:00"):
                for index in range(25):
                    store.create(f"job{index:02d}", {"status": "queued"}, owner_user_id="u")
            seen: list[str] = []
            for offset in range(0, 25, 10):
                rows, total = store.list_jobs(owner_user_id="u", limit=10, offset=offset)
                self.assertEqual(total, 25)
                seen.extend(row["job_id"] for row in rows)
            self.assertEqual(sorted(seen), [f"job{index:02d}" for index in range(25)])

    def test_page_size_is_capped_where_the_endpoint_caps_it(self) -> None:
        self.assertEqual(JOB_PAGE_MAX, 200)


if __name__ == "__main__":
    unittest.main()
