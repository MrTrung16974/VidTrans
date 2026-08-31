import asyncio
import unittest

from application.job_scheduler import JobScheduler


class _FakeService:
    def __init__(self):
        self.jobs = [("job-1", {"resume_request": {"value": 1}})]
        self.updated = []

    def claim_next(self, _worker_id):
        return self.jobs.pop(0) if self.jobs else None

    def update(self, job_id, **fields):
        self.updated.append((job_id, fields))


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


if __name__ == "__main__":
    unittest.main()
