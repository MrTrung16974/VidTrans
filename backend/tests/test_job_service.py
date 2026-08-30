import tempfile
import unittest
from pathlib import Path

from application.job_service import JobService
from infrastructure.job_store import SQLiteJobStore


class JobServiceTests(unittest.TestCase):
    def test_delegates_job_lifecycle_to_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = JobService(SQLiteJobStore(Path(temp_dir) / "jobs.sqlite3"))
            service.create("job-1", {"status": "queued", "progress": 0.0})

            service.update("job-1", status="processing", progress=0.4)

            self.assertEqual(
                service.get("job-1"),
                {"status": "processing", "progress": 0.4},
            )
