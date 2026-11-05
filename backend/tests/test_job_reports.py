"""Problem reports about a video, the admin inbox, and in-app notifications."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from application.job_report_routes import JobReportRouteDependencies, create_job_report_router
from application.job_service import JobService
from infrastructure.job_report_store import JobReportError, JobReportStore
from infrastructure.notification_hub import NotificationHub
from infrastructure.push_notifications import job_message


class JobReportStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = JobReportStore(Path(self.directory.name) / "reports.db")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def create(self, **overrides):
        values = {
            "job_id": "job1", "job_title": "clip.mp4", "reporter_user_id": "u1",
            "reporter_email": "u1@example.com", "category": "translation", "message": "Câu 2 dịch sai",
        }
        return self.store.create(**{**values, **overrides})

    def test_create_list_and_resolve(self) -> None:
        item = self.create(video_time="1:23")
        self.assertEqual(item["status"], "new")
        self.assertEqual(item["category_label"], "Dịch sai / dịch thiếu")
        self.assertEqual(self.store.count_new(), 1)
        resolved = self.store.resolve(item["report_id"], "admin1")
        self.assertEqual((resolved["status"], resolved["resolved_by"]), ("resolved", "admin1"))
        # Resolving twice keeps the first resolver.
        self.assertEqual(self.store.resolve(item["report_id"], "admin2")["resolved_by"], "admin1")
        self.assertEqual(self.store.count_new(), 0)
        items, total = self.store.list(status="resolved")
        self.assertEqual((total, items[0]["report_id"]), (1, item["report_id"]))

    def test_validation(self) -> None:
        with self.assertRaises(JobReportError):
            self.create(category="spam")
        with self.assertRaises(JobReportError):
            self.create(category="other", message="")
        with self.assertRaises(JobReportError):
            self.create(video_time="<script>")
        with self.assertRaises(JobReportError):
            self.store.list(status="bogus")


class JobReportRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = JobReportStore(Path(self.directory.name) / "reports.db")
        self.account = {"user_id": "u1", "email": "u1@example.com", "role": "user"}
        self.jobs = {"job1": {"job_id": "job1", "filename": "clip.mp4", "owner_user_id": "u1"}}
        self.notified: list[dict] = []
        self.audits: list[dict] = []

        def require_admin(_request):
            if self.account["role"] not in {"owner", "admin"}:
                raise HTTPException(status_code=403, detail="forbidden")
            return self.account

        def job_for_request(_request, job_id):
            job = self.jobs.get(job_id)
            if job and (self.account["role"] in {"owner", "admin"} or job["owner_user_id"] == self.account["user_id"]):
                return job
            return None

        app = FastAPI()
        app.include_router(create_job_report_router(JobReportRouteDependencies(
            store=self.store,
            job_for_request=job_for_request,
            current_account=lambda _request: self.account,
            require_admin=require_admin,
            audit=lambda **event: self.audits.append(event),
            notify_staff=self.notified.append,
        )))
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_owner_reports_and_staff_is_notified(self) -> None:
        response = self.client.post("/api/v1/jobs/job1/reports", json={"category": "voice", "video_time": "0:42"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(self.notified), 1)
        notice = self.notified[0]
        self.assertEqual((notice["type"], notice["job_id"]), ("job_report", "job1"))
        self.assertIn("u1@example.com", notice["body"])
        self.assertIn("0:42", notice["body"])

    def test_cannot_report_someone_elses_job(self) -> None:
        self.jobs["job2"] = {"job_id": "job2", "owner_user_id": "u2"}
        self.assertEqual(self.client.post("/api/v1/jobs/job2/reports", json={"category": "video"}).status_code, 404)
        self.assertEqual(self.notified, [])

    def test_invalid_report_is_rejected(self) -> None:
        response = self.client.post("/api/v1/jobs/job1/reports", json={"category": "nope"})
        self.assertEqual(response.status_code, 422)

    def test_rate_limit(self) -> None:
        for _ in range(10):
            self.assertEqual(self.client.post("/api/v1/jobs/job1/reports", json={"category": "video"}).status_code, 201)
        self.assertEqual(self.client.post("/api/v1/jobs/job1/reports", json={"category": "video"}).status_code, 429)

    def test_admin_inbox_requires_staff_and_audits_resolve(self) -> None:
        report_id = self.client.post("/api/v1/jobs/job1/reports", json={"category": "video"}).json()["report_id"]
        self.assertEqual(self.client.get("/api/v1/admin/job-reports").status_code, 403)
        self.account = {"user_id": "a1", "email": "a@example.com", "role": "admin"}
        listing = self.client.get("/api/v1/admin/job-reports").json()
        self.assertEqual((listing["total"], listing["new_count"]), (1, 1))
        resolved = self.client.post(f"/api/v1/admin/job-reports/{report_id}/resolve")
        self.assertEqual(resolved.json()["status"], "resolved")
        self.assertEqual(self.audits[0]["action"], "admin.job_report.resolve")
        self.assertEqual(self.client.post("/api/v1/admin/job-reports/missing/resolve").status_code, 404)


class NotificationHubTests(unittest.TestCase):
    def test_user_and_staff_delivery(self) -> None:
        async def scenario():
            hub = NotificationHub()
            await hub.start()
            owner = hub.subscribe("u1", staff=False)
            other = hub.subscribe("u2", staff=False)
            admin = hub.subscribe("a1", staff=True)
            hub.publish_to_user("u1", {"type": "job_status", "status": "created"})
            hub.publish_to_staff({"type": "job_report"})
            await asyncio.sleep(0)
            got = lambda queue: [queue.get_nowait()["type"] for _ in range(queue.qsize())]  # noqa: E731
            result = (got(owner), got(other), got(admin))
            hub.unsubscribe(owner)
            self.assertEqual(hub.subscriber_count, 2)
            await hub.stop()
            return result

        owner, other, admin = asyncio.run(scenario())
        self.assertEqual(owner, ["job_status"])
        self.assertEqual(other, [])
        self.assertEqual(admin, ["job_report"])

    def test_publish_before_start_is_ignored(self) -> None:
        NotificationHub().publish_to_staff({"type": "job_report"})


class CreatedNotificationTests(unittest.TestCase):
    def test_job_creation_reports_a_created_event(self) -> None:
        events = []

        class Store:
            def create(self, job_id, payload, owner_user_id):
                return {"job_id": job_id, "status": "queued"}

        service = JobService(Store(), on_status=lambda job_id, status: events.append((job_id, status)))
        service.create("job1", {})
        self.assertEqual(events, [("job1", "created")])

    def test_created_message(self) -> None:
        title, body, data = job_message("job1", "created", {"filename": "clip.mp4"})
        self.assertEqual(title, "Đã tạo video")
        self.assertIn("clip.mp4", body)
        self.assertEqual(data["status"], "created")


if __name__ == "__main__":
    unittest.main()
