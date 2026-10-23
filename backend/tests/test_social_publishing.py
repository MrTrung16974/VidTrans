import tempfile
import unittest
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
        app.include_router(create_social_publish_router(
            self.service, jobs, self.video.parent,
            connection_resolver=lambda _user, _connection, _platform: {
                "provider_account_id": "creator-one"
            },
        ))
        client = TestClient(app)

        capabilities = client.get("/api/v1/social-platforms")
        self.assertEqual(capabilities.status_code, 200)
        self.assertEqual(capabilities.json()["items"][0]["platform"], "INSTAGRAM")

        response = client.post(
            "/api/v1/jobs/job-1/social-publishes",
            json={
                "platform": "INSTAGRAM",
                "connection_id": "connection-one",
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

    def test_owner_role_can_publish_another_users_video(self):
        foreign_job = {"status": "completed", "output_video": self.video.name, "owner_user_id": "user-b"}

        class ScopedJobs:
            def get(self, job_id):
                return foreign_job if job_id == "foreign-job" else None

            def get_for_user(self, job_id, owner_user_id):
                job = self.get(job_id)
                return job if job and job["owner_user_id"] == owner_user_id else None

        app = FastAPI()
        app.include_router(create_social_publish_router(
            self.service,
            ScopedJobs(),
            self.video.parent,
            owner_resolver=lambda request: request.headers.get("x-user-id", ""),
            can_manage_all_resolver=lambda request: request.headers.get("x-role") == "owner",
            connection_resolver=lambda _user, _connection, _platform: {"provider_account_id": "creator-one"},
        ))
        client = TestClient(app)
        payload = {
            "platform": "INSTAGRAM", "connection_id": "connection-one",
            "mode": "SCHEDULE_LOCAL", "caption": "Caption",
            "scheduled_for": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
            "timezone": "Asia/Ho_Chi_Minh", "options": {}, "reviewed": True,
            "idempotency_key": "owner-foreign-video-key",
        }

        regular = client.post("/api/v1/jobs/foreign-job/social-publishes", headers={"x-user-id": "user-a", "x-role": "user"}, json=payload)
        owner = client.post("/api/v1/jobs/foreign-job/social-publishes", headers={"x-user-id": "admin", "x-role": "owner"}, json=payload)

        self.assertEqual(regular.status_code, 404)
        self.assertEqual(owner.status_code, 202)

    def test_routes_do_not_expose_another_users_publish_attempt(self):
        jobs = type("Jobs", (), {"get_for_user": lambda _self, job_id, owner: (
            {"status": "completed", "output_video": self.video.name}
            if job_id == "job-1" and owner == "user-a" else None
        )})()
        app = FastAPI()
        app.include_router(create_social_publish_router(
            self.service, jobs, self.video.parent,
            owner_resolver=lambda request: request.headers.get("x-test-user", "user-a"),
            connection_resolver=lambda user_id, connection_id, platform: (
                {"provider_account_id": "creator-one"}
                if (user_id, connection_id, platform) == ("user-a", "connection-a", "INSTAGRAM")
                else (_ for _ in ()).throw(ValueError("not found"))
            ),
        ))
        client = TestClient(app)
        created = client.post(
            "/api/v1/jobs/job-1/social-publishes",
            headers={"x-test-user": "user-a"},
            json={
                "platform": "INSTAGRAM", "connection_id": "connection-a",
                "mode": "SCHEDULE_LOCAL", "caption": "Caption",
                "scheduled_for": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
                "timezone": "Asia/Bangkok", "options": {}, "reviewed": True,
                "idempotency_key": "tenant-route-key-123",
            },
        )
        self.assertEqual(created.status_code, 202)
        attempt_id = created.json()["attempt_id"]
        self.assertEqual(created.json()["connection_id"], "connection-a")
        self.assertEqual(created.json()["account_id"], "creator-one")
        self.assertEqual(
            client.get(f"/api/v1/social-publishes/{attempt_id}", headers={"x-test-user": "user-b"}).status_code,
            404,
        )
        self.assertEqual(
            client.get(f"/api/v1/social-publishes/{attempt_id}", headers={"x-test-user": "user-a"}).status_code,
            200,
        )

if __name__ == "__main__":
    unittest.main()

