import asyncio
import tempfile
import threading
import unittest
from pathlib import Path

from application.job_service import JobService
from infrastructure.job_events import ALL_JOBS, JobEventHub
from infrastructure.job_store import SQLiteJobStore


class JobServiceChangeTests(unittest.TestCase):
    def test_every_job_write_notifies_the_listener(self) -> None:
        changes: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            service = JobService(SQLiteJobStore(Path(temp_dir) / "jobs.sqlite3"), on_change=changes.append)
            service.create("job-1", {"status": "queued", "progress": 0.0})
            service.claim_next("worker-1")
            service.update("job-1", progress=0.5)
            service.request_cancel("job-1")
            service.delete("job-1")
        self.assertEqual(changes, ["job-1"] * 5)

    def test_listener_failure_does_not_fail_the_write(self) -> None:
        def broken(_job_id: str) -> None:
            raise RuntimeError("socket gone")

        with tempfile.TemporaryDirectory() as temp_dir:
            service = JobService(SQLiteJobStore(Path(temp_dir) / "jobs.sqlite3"), on_change=broken)
            service.create("job-1", {"status": "queued"})
            self.assertEqual(service.update("job-1", progress=0.2)["progress"], 0.2)


class JobEventHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_burst_from_worker_threads_becomes_one_message(self) -> None:
        builds: list[set[str]] = []

        def build(job_ids: set[str]) -> dict:
            builds.append(set(job_ids))
            return {"type": "jobs", "items": [{"job_id": job_id} for job_id in sorted(job_ids)], "removed": []}

        hub = JobEventHub(build, interval=0.05)
        await hub.start()
        queue = hub.subscribe()
        try:
            threads = [threading.Thread(target=hub.notify, args=(job_id,)) for job_id in ["a", "b", "a", "a"]]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            message = await asyncio.wait_for(queue.get(), timeout=2)
            self.assertEqual([item["job_id"] for item in message["items"]], ["a", "b"])
            self.assertEqual(builds, [{"a", "b"}])
        finally:
            hub.unsubscribe(queue)
            await hub.stop()

    async def test_no_database_work_without_subscribers(self) -> None:
        builds: list[set[str]] = []
        hub = JobEventHub(lambda job_ids: builds.append(job_ids) or {}, interval=0.01)
        await hub.start()
        try:
            hub.notify("a")
            await asyncio.sleep(0.1)
            self.assertEqual(builds, [])
        finally:
            await hub.stop()

    async def test_slow_subscriber_gets_one_resync_instead_of_a_backlog(self) -> None:
        hub = JobEventHub(lambda job_ids: {"type": "jobs", "items": [], "removed": []}, interval=0.01, queue_size=2)
        queue = hub.subscribe()
        for _ in range(3):
            hub._publish({"type": "jobs", "items": [], "removed": []})
        self.assertEqual(queue.qsize(), 1)
        self.assertTrue(queue.get_nowait()["resync"])

    def test_notify_before_start_is_safe(self) -> None:
        hub = JobEventHub(lambda job_ids: {})
        hub.notify(ALL_JOBS)
        self.assertEqual(hub._take_dirty(), {ALL_JOBS})


if __name__ == "__main__":
    unittest.main()
