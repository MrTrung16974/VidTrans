import tempfile
import unittest
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.social_publish_routes import create_social_publish_router
from application.social_publishing import (
    SocialPublishingService,
    SocialPublishingValidationError,
)
from domain.social_publishing import (
    PublishRequest,
    PublishResult,
    PublishStatus,
    PublisherCapabilities,
    SocialPlatform,
)
from infrastructure.publishers.registry import PublisherRegistry
from infrastructure.social_publish_store import SQLiteSocialPublishStore


class FakeInstagramPublisher:
    platform = SocialPlatform.INSTAGRAM
    capabilities = PublisherCapabilities(caption_limit=100, schedule_horizon_days=30)

    def __init__(self, *, fail_after_checkpoint: bool = False) -> None:
        self.calls: list[PublishRequest] = []
        self.fail_after_checkpoint = fail_after_checkpoint

    def publish(self, request, *, on_checkpoint, on_progress=None):
        self.calls.append(request)
        on_checkpoint("media-123", None)
        if self.fail_after_checkpoint:
            raise TimeoutError("provider timeout")
        if on_progress:
            on_progress(5, 5)
        on_checkpoint("media-123", "post-123")
        return PublishResult(
            status=PublishStatus.PUBLISHED,
            remote_media_id="media-123",
            remote_post_id="post-123",
            status_detail={"provider_status": "FINISHED"},
        )

    def reconcile(self, *, remote_media_id, remote_post_id, account_id):
        return PublishResult(
            status=PublishStatus.PUBLISHED,
            remote_media_id=remote_media_id,
            remote_post_id=remote_post_id or "post-reconciled",
        )


class SocialPublishingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.video = root / "video.mp4"
        self.video.write_bytes(b"video")
        self.store = SQLiteSocialPublishStore(root / "jobs.sqlite3")
        self.publisher = FakeInstagramPublisher()
        self.registry = PublisherRegistry([self.publisher])
        self.service = SocialPublishingService(self.store, self.registry)

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, **overrides):
        values = {
            "job_id": "job-1",
            "platform": "INSTAGRAM",
            "video_path": self.video,
            "caption": "Caption đã duyệt",
            "mode": "DIRECT_NOW",
            "scheduled_for": None,
            "timezone_name": "Asia/Bangkok",
            "idempotency_key": "request-key-123",
            "account_id": "creator-one",
            "options": {"share_to_feed": True},
        }
        values.update(overrides)
        return self.service.create(**values)

    def test_registry_exposes_capabilities(self):
        self.assertEqual(self.registry.capabilities()[0]["platform"], "INSTAGRAM")
        self.assertEqual(self.registry.capabilities()[0]["caption_limit"], 100)

    def test_idempotency_is_scoped_by_platform_and_account(self):
        first, first_created = self.create()
        second, second_created = self.create(caption="Khác")

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["attempt_id"], second["attempt_id"])
        self.assertEqual(second["caption"], "Caption đã duyệt")

        third, third_created = self.create(account_id="creator-two")
        self.assertTrue(third_created)
        self.assertNotEqual(first["attempt_id"], third["attempt_id"])

    def test_direct_publish_checkpoints_and_completes(self):
        attempt, _ = self.create()
        result = self.service.run(attempt["attempt_id"])

        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(result["remote_media_id"], "media-123")
        self.assertEqual(result["remote_post_id"], "post-123")
        self.assertEqual(self.publisher.calls[0].caption, "Caption đã duyệt")
        self.assertEqual(result["status_detail"]["provider_status"], "FINISHED")

    def test_failure_after_checkpoint_requires_reconciliation(self):
        publisher = FakeInstagramPublisher(fail_after_checkpoint=True)
        service = SocialPublishingService(self.store, PublisherRegistry([publisher]))
        attempt, _ = self.create()

        failed = service.run(attempt["attempt_id"])
        self.assertEqual(failed["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(failed["remote_media_id"], "media-123")

        recovered = service.reconcile(attempt["attempt_id"])
        self.assertEqual(recovered["status"], "PUBLISHED")
        self.assertEqual(recovered["remote_post_id"], "post-reconciled")

    def test_schedule_claim_is_atomic_and_cancel_is_guarded(self):
        future = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
        scheduled, _ = self.create(
            mode="SCHEDULE_LOCAL",
            scheduled_for=future,
            idempotency_key="scheduled-key-123",
        )
        self.assertIsNone(self.store.claim_due())
        self.assertEqual(self.store.cancel(scheduled["attempt_id"])["status"], "CANCELLED")

        due, _ = self.store.create(
            job_id="job-2",
            platform="INSTAGRAM",
            account_id="creator-one",
            mode="SCHEDULE_LOCAL",
            status="SCHEDULED_LOCAL",
            scheduled_for=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            idempotency_key="due-key-123",
            payload={"video_path": str(self.video), "caption": "caption", "options": {}},
        )
        claimed = self.store.claim_due()
        self.assertEqual(claimed["attempt_id"], due["attempt_id"])
        self.assertEqual(claimed["status"], "CLAIMED")
        self.assertIsNone(self.store.claim_due())

    def test_provider_capability_validation(self):
        with self.assertRaisesRegex(SocialPublishingValidationError, "100"):
            self.create(caption="x" * 101)
        with self.assertRaisesRegex(SocialPublishingValidationError, "bản nháp"):
            self.create(mode="DRAFT", idempotency_key="draft-key-123")

    def test_routes_hide_sensitive_publish_fields(self):
        jobs = type("Jobs", (), {"get": lambda _self, _job_id: {
            "status": "completed", "output_video": self.video.name
        }})()
        app = FastAPI()
        app.include_router(create_social_publish_router(self.service, jobs, self.video.parent))
        client = TestClient(app)

        capabilities = client.get("/api/v1/social-platforms")
        self.assertEqual(capabilities.status_code, 200)
        self.assertEqual(capabilities.json()["items"][0]["platform"], "INSTAGRAM")

        response = client.post(
            "/api/v1/jobs/job-1/social-publishes",
            json={
                "platform": "INSTAGRAM",
                "account_id": "creator-one",
                "mode": "SCHEDULE_LOCAL",
                "caption": "Caption",
                "scheduled_for": (
                    datetime.now(timezone.utc) + timedelta(minutes=2)
                ).isoformat(),
                "timezone": "Asia/Bangkok",
                "options": {"share_to_feed": True},
                "reviewed": True,
                "idempotency_key": "route-key-123",
            },
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertNotIn("video_path", payload)
        self.assertNotIn("idempotency_key", payload)
        self.assertNotIn("options", payload)
        self.assertEqual(payload["status"], "SCHEDULED_LOCAL")

    def test_terminal_legacy_tiktok_history_migrates_once(self):
        with sqlite3.connect(self.store.database_path) as connection:
            connection.execute(
                """
                CREATE TABLE tiktok_publish_attempts (
                    attempt_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                    job_id TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
                    scheduled_for TEXT, payload_json TEXT NOT NULL,
                    remote_publish_id TEXT, claimed_at TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO tiktok_publish_attempts VALUES (
                    'old-1', 'old-key-123', 'job-old', 'DIRECT_NOW', 'PUBLISH_COMPLETE',
                    NULL, ?, 'remote-old', NULL, '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:01:00+00:00'
                )
                """,
                (json.dumps({
                    "caption": "old", "video_path": str(self.video),
                    "account_id": "creator-old", "status": "payload-must-not-shadow",
                    "status_detail": {"status": "PUBLISH_COMPLETE"},
                }),),
            )

        self.assertEqual(self.store.migrate_legacy_tiktok_history(), 1)
        self.assertEqual(self.store.migrate_legacy_tiktok_history(), 0)
        migrated = self.store.list_for_job("job-old")[0]
        self.assertEqual(migrated["platform"], "TIKTOK")
        self.assertEqual(migrated["status"], "PUBLISHED")
        self.assertEqual(migrated["account_id"], "creator-old")
        self.assertEqual(migrated["remote_media_id"], "remote-old")


if __name__ == "__main__":
    unittest.main()

