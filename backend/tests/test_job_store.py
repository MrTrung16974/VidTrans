import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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

    def test_jobs_and_batches_are_isolated_by_owner(self) -> None:
        self.store.create_batch("batch-a", name="A", config={"mode": 1}, owner_user_id="user-a")
        self.store.create_batch("batch-b", name="B", config={"mode": 2}, owner_user_id="user-b")
        self.store.create("job-a", {"status": "queued", "filename": "a.mp4"}, owner_user_id="user-a")
        self.store.create("job-b", {"status": "queued", "filename": "b.mp4"}, owner_user_id="user-b")
        self.store.attach_to_batch("job-a", "batch-a")
        self.store.attach_to_batch("job-b", "batch-b")

        jobs_a, total_a = self.store.list_jobs(owner_user_id="user-a")
        batches_a, batch_total_a = self.store.list_batches(owner_user_id="user-a")

        self.assertEqual(total_a, 1)
        self.assertEqual([job["job_id"] for job in jobs_a], ["job-a"])
        self.assertIsNone(self.store.get_for_user("job-b", "user-a"))
        self.assertFalse(self.store.owns_artifact("user-a", "b.mp4"))
        self.assertTrue(self.store.owns_artifact("user-a", "a.mp4"))
        self.assertEqual(batch_total_a, 1)
        self.assertEqual([batch["batch_id"] for batch in batches_a], ["batch-a"])
        self.assertIsNone(self.store.get_batch_for_user("batch-b", "user-a"))

    def test_job_cannot_attach_to_another_owners_batch(self) -> None:
        self.store.create_batch("batch-a", name="A", config={}, owner_user_id="user-a")
        self.store.create("job-b", {"status": "queued"}, owner_user_id="user-b")
        with self.assertRaisesRegex(Exception, "owners do not match"):
            self.store.attach_to_batch("job-b", "batch-a")

    def test_backfill_migrates_legacy_batch_before_its_jobs(self) -> None:
        self.store.create_batch("legacy-batch", name="Legacy", config={})
        self.store.create("legacy-job", {"status": "completed", "filename": "old.mp4"})
        self.store.attach_to_batch("legacy-job", "legacy-batch")

        self.store.backfill_legacy_owner("owner-a")

        self.assertEqual(self.store.get("legacy-job")["owner_user_id"], "owner-a")
        self.assertEqual(self.store.get_batch("legacy-batch")["owner_user_id"], "owner-a")

    def test_database_triggers_lock_owner_and_batch_tenant(self) -> None:
        self.store.create_batch("batch-a", name="A", config={}, owner_user_id="user-a")
        self.store.create("job-a", {"status": "queued"}, owner_user_id="user-a")
        with self.store._connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE jobs SET owner_user_id='user-b' WHERE job_id='job-a'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE jobs SET batch_id='missing' WHERE job_id='job-a'")

    def test_prune_terminal_keeps_active_and_recent_jobs(self) -> None:
        self.store.create_batch("old-batch", name="Old", config={}, owner_user_id="user-a")
        self.store.create("old-done", {"status": "completed"}, owner_user_id="user-a")
        self.store.attach_to_batch("old-done", "old-batch")
        self.store.create("recent-done", {"status": "completed"}, owner_user_id="user-a")
        self.store.create("old-active", {"status": "processing"}, owner_user_id="user-a")
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with self.store._connection() as conn:
            conn.execute("UPDATE jobs SET updated_at=? WHERE job_id IN ('old-done','old-active')", (old,))
            conn.execute("UPDATE batches SET updated_at=? WHERE batch_id='old-batch'", (old,))

        removed = self.store.prune_terminal(cutoff)

        self.assertEqual([item["job_id"] for item in removed["jobs"]], ["old-done"])
        self.assertEqual([item["batch_id"] for item in removed["batches"]], ["old-batch"])
        self.assertIsNotNone(self.store.get("recent-done"))
        self.assertIsNotNone(self.store.get("old-active"))

    def test_prune_terminal_scopes_by_owner_and_keeps_pending_publishes(self) -> None:
        for job_id, owner in (("a-old", "user-a"), ("b-old", "user-b"), ("a-scheduled", "user-a")):
            self.store.create(job_id, {"status": "completed"}, owner_user_id=owner)
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        with self.store._connection() as conn:
            conn.execute("UPDATE jobs SET updated_at=?", (old,))
            conn.execute("CREATE TABLE social_publish_attempts (job_id TEXT, status TEXT)")
            conn.execute("INSERT INTO social_publish_attempts VALUES ('a-scheduled','SCHEDULED_LOCAL')")

        removed = self.store.prune_terminal(cutoff, owner_user_id="user-a")
        self.assertEqual([item["job_id"] for item in removed["jobs"]], ["a-old"])
        self.assertIsNotNone(self.store.get("b-old"))
        self.assertIsNotNone(self.store.get("a-scheduled"))

        removed = self.store.prune_terminal(cutoff, exclude_owner_user_ids=["user-b"])
        self.assertEqual(removed["jobs"], [])
        self.assertIsNotNone(self.store.get("b-old"))

    def test_triggers_reject_empty_or_null_owner(self) -> None:
        import sqlite3
        with self.store._connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO jobs(job_id, status, payload_json, owner_user_id, created_at, updated_at) VALUES ('bad1', 'queued', '{}', NULL, 'now', 'now')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO jobs(job_id, status, payload_json, owner_user_id, created_at, updated_at) VALUES ('bad2', 'queued', '{}', '   ', 'now', 'now')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO batches(batch_id, name, status, config_json, owner_user_id, created_at, updated_at) VALUES ('bad-b1', 'B', 'queued', '{}', NULL, 'now', 'now')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO batches(batch_id, name, status, config_json, owner_user_id, created_at, updated_at) VALUES ('bad-b2', 'B', 'queued', '{}', '', 'now', 'now')")

    def test_claim_next_is_atomic_and_honors_priority(self) -> None:
        self.store.create("old", {"status": "queued"})
        self.store.create("urgent", {"status": "queued"})
        self.store.create_batch("batch-x", name="Priority", config={})
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

        self.assertEqual(queued["status"], "cancelled")
        self.assertEqual(running["status"], "cancelled")
        self.assertTrue(self.store.is_cancel_requested("queued-cancel"))
        self.assertTrue(self.store.is_cancel_requested("running-cancel"))

    def test_cancelled_job_ignores_late_worker_updates(self) -> None:
        self.store.create("late-worker", {"status": "queued", "progress": 0.0})
        self.store.claim_next("worker")
        cancelled = self.store.request_cancel("late-worker")

        late = self.store.update(
            "late-worker",
            status="completed",
            progress=1.0,
            step="completed",
        )

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(late["status"], "cancelled")
        self.assertNotEqual(late["progress"], 1.0)

    def test_claims_due_schedule_before_regular_queue(self) -> None:
        self.store.create("regular", {"status": "queued"})
        self.store.create("due", {"status": "queued"})
        due_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.store.update(
            "due",
            status="scheduled",
            tiktok_publish_at=due_at,
            job_action="publish_tiktok",
            publish_request={"video_path": "output.mp4", "title": "Title"},
        )

        claimed = self.store.claim_next("worker")

        self.assertEqual(claimed[0], "due")
        self.assertEqual(claimed[1]["step"], "publishing-tiktok")

    def test_future_schedule_is_not_claimed_and_can_be_cancelled(self) -> None:
        self.store.create("future", {"status": "queued"})
        future_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.store.update(
            "future",
            status="scheduled",
            tiktok_publish_at=future_at,
            job_action="publish_tiktok",
            publish_request={"video_path": "output.mp4", "title": "Title"},
        )

        self.assertIsNone(self.store.claim_next("worker"))
        cancelled = self.store.request_cancel("future")
        self.assertEqual(cancelled["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
