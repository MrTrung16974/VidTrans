import sqlite3
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

    def test_migrates_legacy_jobs_table_without_losing_payload(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO jobs VALUES ('legacy', 'completed', '{\"status\":\"completed\"}', 'a', 'b')"
            )
            connection.commit()
        finally:
            connection.close()

        migrated = SQLiteJobStore(legacy_path)

        self.assertEqual(migrated.get("legacy"), {"status": "completed"})
        jobs, total = migrated.list_jobs()
        self.assertEqual(total, 1)
        self.assertEqual(jobs[0]["job_id"], "legacy")

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

    def test_requeue_returns_unfinished_jobs_to_queue(self) -> None:
        self.store.create("queued", {"status": "queued", "progress": 0.0})
        self.store.create("running", {"status": "processing", "progress": 0.5})
        self.store.create("done", {"status": "completed", "progress": 1.0})

        requeued = self.store.requeue_interrupted()

        self.assertEqual({job_id for job_id, _ in requeued}, {"queued", "running"})
        self.assertEqual(self.store.get("queued")["status"], "queued")
        self.assertEqual(self.store.get("running")["step"], "queued")
        self.assertEqual(self.store.get("running")["restart_count"], 1)
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

    def test_batch_listing_and_job_filtering(self) -> None:
        self.store.create_batch("batch-1", name="Đợt video", config={"mode": 1})
        self.store.create("job-a", {"status": "queued", "filename": "a.mp4"})
        self.store.create("job-b", {"status": "completed", "filename": "b.mp4"})
        self.store.attach_to_batch("job-a", "batch-1")
        self.store.attach_to_batch("job-b", "batch-1")

        jobs, total = self.store.list_jobs(batch_id="batch-1", status="queued")
        batches, batch_total = self.store.list_batches()

        self.assertEqual(total, 1)
        self.assertEqual(jobs[0]["job_id"], "job-a")
        self.assertEqual(batch_total, 1)
        self.assertEqual(batches[0]["counts"], {"completed": 1, "queued": 1})

    def test_claim_next_is_atomic_and_honors_priority(self) -> None:
        self.store.create("old", {"status": "queued"})
        self.store.create("urgent", {"status": "queued"})
        self.store.attach_to_batch("urgent", "batch-x", priority=10)

        claimed = self.store.claim_next("worker-1")

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed[0], "urgent")
        self.assertEqual(self.store.get("urgent")["status"], "processing")
        self.assertEqual(self.store.claim_next("worker-2")[0], "old")
        self.assertIsNone(self.store.claim_next("worker-3"))

    def test_cancel_queued_and_running_jobs(self) -> None:
        self.store.create("queued-cancel", {"status": "queued"})
        self.store.create("running-cancel", {"status": "queued"})
        self.store.claim_next("worker")

        queued = self.store.request_cancel("running-cancel")
        running = self.store.request_cancel("queued-cancel")

        statuses = {queued["status"], running["status"]}
        self.assertEqual(statuses, {"cancelled", "cancelling"})
        self.assertTrue(self.store.is_cancel_requested("queued-cancel"))
        self.assertTrue(self.store.is_cancel_requested("running-cancel"))


if __name__ == "__main__":
    unittest.main()
