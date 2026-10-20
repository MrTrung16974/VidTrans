import asyncio
import unittest

from application.job_scheduler import JobScheduler


class _FakeService:
    def __init__(self):
        self.jobs = [("job-1", {"resume_request": {"value": 1}})]
        self.updated = []
        self.cancelling = set()

    def claim_next(self, _worker_id):
        return self.jobs.pop(0) if self.jobs else None

    def update(self, job_id, **fields):
        self.updated.append((job_id, fields))

    def finalize_cancellation(self, job_id, **_fields):
        finalized = job_id in self.cancelling
        self.cancelling.discard(job_id)
        return {"status": "cancelled" if finalized else "completed"}, finalized


class JobSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_workers_claim_and_run_persisted_jobs(self) -> None:
        service = _FakeService()
        completed = asyncio.Event()

        def runner(job_id, request):
            self.assertEqual(job_id, "job-1")
            self.assertEqual(request, {"value": 1})
            completed_loop.call_soon_threadsafe(completed.set)

        completed_loop = asyncio.get_running_loop()
        scheduler = JobScheduler(service, runner, concurrency=2, poll_interval=0.01)
        await scheduler.start()
        await asyncio.wait_for(completed.wait(), timeout=1)
        await scheduler.stop()

        self.assertEqual(service.jobs, [])
        self.assertEqual(service.updated, [])

    async def test_invalid_persisted_request_is_failed(self) -> None:
        service = _FakeService()
        service.jobs = [("bad-job", {"resume_request": None})]
        scheduler = JobScheduler(service, lambda *_: None, concurrency=1, poll_interval=0.01)
        await scheduler.start()
        for _ in range(50):
            if service.updated:
                break
            await asyncio.sleep(0.01)
        await scheduler.stop()

        self.assertEqual(service.updated[0][0], "bad-job")
        self.assertEqual(service.updated[0][1]["status"], "failed")

    async def test_scheduled_publish_uses_publish_request(self) -> None:
        service = _FakeService()
        service.jobs = [
            (
                "scheduled-job",
                {
                    "job_action": "publish_tiktok",
                    "publish_request": {"video_path": "output.mp4", "title": "Xin chào"},
                    "resume_request": {"value": "must-not-run"},
                },
            )
        ]
        completed = asyncio.Event()
        completed_loop = asyncio.get_running_loop()

        def runner(job_id, request):
            self.assertEqual(job_id, "scheduled-job")
            self.assertEqual(request["_job_action"], "publish_tiktok")
            self.assertEqual(request["title"], "Xin chào")
            completed_loop.call_soon_threadsafe(completed.set)

        scheduler = JobScheduler(service, runner, concurrency=1, poll_interval=0.01)
        await scheduler.start()
        await asyncio.wait_for(completed.wait(), timeout=1)
        await scheduler.stop()

        self.assertEqual(service.updated, [])

    async def test_worker_acknowledges_cancelling_job_after_runner_returns(self) -> None:
        service = _FakeService()
        service.cancelling.add("job-1")
        released = []
        acknowledged = asyncio.Event()
        loop = asyncio.get_running_loop()

        def on_cancelled(job_id):
            released.append(job_id)
            loop.call_soon_threadsafe(acknowledged.set)

        def runner(job_id, _request):
            raise RuntimeError("late failure while cancelling")

        scheduler = JobScheduler(
            service, runner, concurrency=1, poll_interval=0.01, on_cancelled=on_cancelled
        )
        await scheduler.start()
        await asyncio.wait_for(acknowledged.wait(), timeout=1)
        await scheduler.stop()

        self.assertEqual(released, ["job-1"])
        self.assertEqual(service.cancelling, set())

    async def test_completed_job_is_not_released_by_acknowledgement(self) -> None:
        service = _FakeService()
        released = []
        completed = asyncio.Event()
        loop = asyncio.get_running_loop()

        def runner(_job_id, _request):
            loop.call_soon_threadsafe(completed.set)

        scheduler = JobScheduler(
            service, runner, concurrency=1, poll_interval=0.01, on_cancelled=released.append
        )
        await scheduler.start()
        await asyncio.wait_for(completed.wait(), timeout=1)
        await asyncio.sleep(0.05)
        await scheduler.stop()

        self.assertEqual(released, [])


if __name__ == "__main__":
    unittest.main()
