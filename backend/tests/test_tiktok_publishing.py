import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.tiktok_publish_routes import create_tiktok_publish_router
from application.tiktok_publishing import TikTokPublishingService
from infrastructure.tiktok_publish_store import SQLiteTikTokPublishStore


class FakePublisher:
    def __init__(self, *, fail_after_init: bool = False) -> None:
        self.calls = []
        self.fail_after_init = fail_after_init

    def publish(self, path, caption, *, privacy_level, on_initialized):
        self.calls.append(("publish", path, caption, privacy_level))
        on_initialized("remote-123")
        if self.fail_after_init:
            raise RuntimeError("network timeout")
        return {
            "publish_id": "remote-123",
            "status": "PROCESSING_UPLOAD",
            "status_payload": {"status": "PROCESSING_UPLOAD"},
        }

    def upload_draft(self, path, *, on_initialized):
        self.calls.append(("draft", path))
        on_initialized("draft-123")
        return {
            "publish_id": "draft-123",
            "status": "SEND_TO_USER_INBOX",
            "status_payload": {"status": "SEND_TO_USER_INBOX"},
        }

    def fetch_status(self, publish_id):
        return {"status": "PUBLISH_COMPLETE", "publish_id": publish_id}


class TikTokPublishingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.video = root / "video.mp4"
        self.video.write_bytes(b"video")
        self.store = SQLiteTikTokPublishStore(root / "jobs.sqlite3")
        self.publisher = FakePublisher()
        self.service = TikTokPublishingService(self.store, self.publisher)

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, **overrides):
        values = {
            "job_id": "job-1",
            "video_path": self.video,
            "caption": "Dòng một\n\n#xuhuong #video",
            "mode": "DIRECT_NOW",
            "privacy_level": "SELF_ONLY",
            "scheduled_for": None,
            "timezone_name": "Asia/Ho_Chi_Minh",
            "idempotency_key": "request-key-123",
        }
        values.update(overrides)
        return self.service.create(**values)

    def test_same_idempotency_key_returns_existing_attempt(self):
        first, first_created = self.create()
        second, second_created = self.create(caption="Nội dung khác")

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["attempt_id"], second["attempt_id"])
        self.assertEqual(second["caption"], "Dòng một\n\n#xuhuong #video")

    def test_idempotency_key_cannot_be_reused_for_another_job(self):
        self.create()

        with self.assertRaisesRegex(Exception, "video khác"):
            self.create(job_id="job-2")

    def test_second_active_attempt_for_same_video_is_rejected(self):
        self.create()

        with self.assertRaisesRegex(Exception, "đang hoạt động"):
            self.create(idempotency_key="another-key-123")

    def test_direct_publish_preserves_reviewed_caption(self):
        attempt, _ = self.create()

        result = self.service.run(attempt["attempt_id"])

        self.assertEqual(self.publisher.calls[0][2], "Dòng một\n\n#xuhuong #video")
        self.assertEqual(result["remote_publish_id"], "remote-123")
        self.assertEqual(result["status"], "PROCESSING_UPLOAD")

    def test_failure_after_remote_init_requires_reconciliation(self):
        publisher = FakePublisher(fail_after_init=True)
        service = TikTokPublishingService(self.store, publisher)
        attempt, _ = self.create(idempotency_key="ambiguous-123")

        result = service.run(attempt["attempt_id"])

        self.assertEqual(result["remote_publish_id"], "remote-123")
        self.assertEqual(result["status"], "NEEDS_RECONCILIATION")

    def test_due_schedule_is_claimed_once_and_can_be_cancelled_before_claim(self):
        future = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
        attempt, _ = self.create(
            mode="SCHEDULE",
            scheduled_for=future,
            idempotency_key="scheduled-123",
        )
        self.assertIsNone(self.store.claim_due())
        cancelled = self.store.cancel(attempt["attempt_id"])
        self.assertEqual(cancelled["status"], "CANCELLED")

        due, _ = self.store.create(
            idempotency_key="due-key-123",
            job_id="job-1",
            mode="SCHEDULE",
            status="SCHEDULED_LOCAL",
            scheduled_for=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            payload={"video_path": str(self.video), "caption": "caption"},
        )
        claimed = self.store.claim_due()
        self.assertEqual(claimed["attempt_id"], due["attempt_id"])
        self.assertEqual(claimed["status"], "UPLOADING")
        self.assertIsNone(self.store.claim_due())

    def test_route_does_not_expose_server_path_or_idempotency_key(self):
        jobs = type("Jobs", (), {"get": lambda _self, _job_id: {
            "status": "completed", "output_video": self.video.name
        }})()
        app = FastAPI()
        app.include_router(create_tiktok_publish_router(self.service, jobs, self.video.parent))
        client = TestClient(app)
        response = client.post(
            "/api/v1/jobs/job-1/tiktok-publishes",
            data={
                "caption": "Nội dung\n\n#tag",
                "mode": "SCHEDULE",
                "privacy_level": "SELF_ONLY",
                "scheduled_for": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
                "timezone_name": "Asia/Ho_Chi_Minh",
                "idempotency_key": "route-request-123",
                "reviewed": "true",
            },
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertNotIn("video_path", payload)
        self.assertNotIn("idempotency_key", payload)
        self.assertEqual(payload["status"], "SCHEDULED_LOCAL")


if __name__ == "__main__":
    unittest.main()
