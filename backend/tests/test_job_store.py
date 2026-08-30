import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from infrastructure.job_store import (
    JobAlreadyExistsError,
    JobNotFoundError,
    SQLiteJobStore,
)


class SQLiteJobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "jobs.sqlite3"
        self.store = SQLiteJobStore(self.database_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_and_get_survive_new_store_instance(self) -> None:
        self.store.create(
            "job-1",
            {
                "status": "queued",
                "filename": "视频.mp4",
                "artifact": Path("outputs/result.mp4"),
            },
        )

        reopened = SQLiteJobStore(self.database_path)
        self.assertEqual(
            reopened.get("job-1"),
            {
                "status": "queued",
                "filename": "视频.mp4",
                "artifact": str(Path("outputs/result.mp4")),
            },
        )

    def test_update_atomically_merges_fields(self) -> None:
        self.store.create("job-2", {"status": "queued", "progress": 0.0, "mode": 1})
        updated = self.store.update(
            "job-2",
            status="processing",
            progress=0.4,
            step="translating",
        )

        self.assertEqual(updated["mode"], 1)
        self.assertEqual(updated["status"], "processing")
        self.assertEqual(self.store.get("job-2")["step"], "translating")

    def test_duplicate_job_is_rejected(self) -> None:
        self.store.create("same-id", {"status": "queued"})
        with self.assertRaises(JobAlreadyExistsError):
            self.store.create("same-id", {"status": "queued"})

    def test_updating_unknown_job_is_rejected(self) -> None:
        with self.assertRaises(JobNotFoundError):
            self.store.update("missing", status="failed")

    def test_recovery_only_marks_unfinished_jobs(self) -> None:
        self.store.create("queued", {"status": "queued", "progress": 0.0})
        self.store.create("running", {"status": "processing", "progress": 0.5})
        self.store.create("done", {"status": "completed", "progress": 1.0})

        recovered = self.store.recover_interrupted("test restart")

        self.assertEqual(recovered, 2)
        self.assertEqual(self.store.get("queued")["step"], "interrupted")
        self.assertEqual(self.store.get("running")["error"], "test restart")
        self.assertEqual(self.store.get("done")["status"], "completed")

    def test_concurrent_updates_do_not_lose_fields(self) -> None:
        self.store.create("concurrent", {"status": "processing"})

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(self.store.update, "concurrent", **{f"metric_{index}": index})
                for index in range(20)
            ]
            for future in futures:
                future.result()

        stored = self.store.get("concurrent")
        for index in range(20):
            self.assertEqual(stored[f"metric_{index}"], index)


if __name__ == "__main__":
    unittest.main()
