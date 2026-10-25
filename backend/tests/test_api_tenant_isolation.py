import asyncio
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request

import main
from application.job_service import JobService
from infrastructure.job_store import SQLiteJobStore


def account(user_id: str, role: str = "editor") -> dict[str, str]:
    return {
        "user_id": user_id,
        "email": f"{user_id}@test.local",
        "display_name": user_id,
        "role": role,
        "plan": "free",
        "approval_status": "approved",
    }


def request_for(value: dict[str, str], method: str = "GET") -> Request:
    request = Request({"type": "http", "method": method, "path": "/", "headers": []})
    request.state.account = value
    return request


def response_json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


class ApiTenantIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.output_dir = root / "outputs"
        self.upload_dir = root / "uploads"
        self.work_dir = root / "work"
        self.output_dir.mkdir()
        self.upload_dir.mkdir()
        self.work_dir.mkdir()
        self.service = JobService(SQLiteJobStore(root / "jobs.sqlite3"))
        self.user_a = account("user-a")
        self.user_b = account("user-b")
        self.admin = account("admin", "admin")
        self.service.create_batch("batch-a", name="A", config={"secret": "a"}, owner_user_id="user-a")
        artifact = self.output_dir / "users" / "user-a" / "job-a" / "video.mp4"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"video-a")
        self.service.create(
            "job-a",
            {"status": "completed", "filename": "a.mp4", "output_video": artifact.relative_to(self.output_dir).as_posix()},
            owner_user_id="user-a",
        )
        self.service.attach_to_batch("job-a", "batch-a")
        self.service.create("job-b", {"status": "completed", "filename": "b.mp4"}, owner_user_id="user-b")
        self.patches = [
            patch.object(main, "JOB_SERVICE", self.service),
            patch.object(main, "OUTPUT_DIR", self.output_dir),
            patch.object(main, "UPLOAD_DIR", self.upload_dir),
            patch.object(main, "WORK_DIR", self.work_dir),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp.cleanup()

    def test_user_lists_are_scoped_and_admin_sees_all_jobs(self) -> None:
        for identity, expected_jobs in (
            (self.user_a, {"job-a"}),
            (self.user_b, {"job-b"}),
            (self.admin, {"job-a", "job-b"}),
        ):
            payload = response_json(main.list_jobs_endpoint(
                request_for(identity), status=None, batch_id=None, search=None, job_action=None, page=None, limit=50, offset=0
            ))
            self.assertEqual({item["job_id"] for item in payload["items"]}, expected_jobs)
            self.assertTrue(all(item.get("owner_user_id") for item in payload["items"]))

        with self.assertRaises(HTTPException) as denied:
            main.get_job_endpoint(request_for(self.user_b), "job-a")
        self.assertEqual(denied.exception.status_code, 404)

        with self.assertRaises(HTTPException) as denied_batch:
            main.get_batch_endpoint(request_for(self.user_b), "batch-a")
        self.assertEqual(denied_batch.exception.status_code, 404)

        admin_job = response_json(main.get_job_endpoint(request_for(self.admin), "job-a"))
        self.assertEqual(admin_job["owner_user_id"], "user-a")
        admin_batch = response_json(main.get_batch_endpoint(request_for(self.admin), "batch-a"))
        self.assertEqual(admin_batch["batch_id"], "batch-a")

    def test_cross_user_artifact_and_zip_are_hidden(self) -> None:
        with self.assertRaises(HTTPException) as denied_file:
            main.download_job_artifact(request_for(self.user_b), "job-a", "video")
        self.assertEqual(denied_file.exception.status_code, 404)

        with self.assertRaises(HTTPException) as denied_zip:
            main.download_all_job_artifacts(request_for(self.user_b), "job-a")
        self.assertEqual(denied_zip.exception.status_code, 404)

        with self.assertRaises(HTTPException) as denied_legacy:
            main.download(request_for(self.user_b), "video.mp4")
        self.assertEqual(denied_legacy.exception.status_code, 404)

        response = main.download_job_artifact(request_for(self.user_a), "job-a", "video")
        self.assertEqual(Path(response.path).read_bytes(), b"video-a")

    def test_cross_user_mutations_return_404_without_changing_job(self) -> None:
        before = self.service.get("job-a")
        operations = (
            lambda: main.cancel_job_endpoint(request_for(self.user_b, "POST"), "job-a"),
            lambda: main.retry_job_endpoint(request_for(self.user_b, "POST"), "job-a"),
            lambda: main.delete_job_endpoint(request_for(self.user_b, "DELETE"), "job-a"),
            lambda: main.refresh_tiktok_publish_status(request_for(self.user_b, "POST"), "job-a"),
        )
        for operation in operations:
            with self.assertRaises(HTTPException) as denied:
                operation()
            self.assertEqual(denied.exception.status_code, 404)
        self.assertEqual(self.service.get("job-a"), before)

    def test_admin_can_download_and_delete_another_users_job(self) -> None:
        response = main.download_job_artifact(request_for(self.admin), "job-a", "video")
        self.assertEqual(Path(response.path).read_bytes(), b"video-a")
        governance = Mock()
        browser = Mock()
        browser.active_attempt.return_value = None
        with patch.object(main, "TENANT_GOVERNANCE", governance), patch.object(main, "TIKTOK_BROWSER", browser):
            payload = response_json(main.delete_job_endpoint(request_for(self.admin, "DELETE"), "job-b"))
        self.assertTrue(payload["deleted"])
        self.assertIsNone(self.service.get("job-b"))

    def test_delete_is_soft_and_keeps_artifacts(self) -> None:
        governance = Mock()
        browser = Mock()
        browser.active_attempt.return_value = None
        with patch.object(main, "TENANT_GOVERNANCE", governance), patch.object(main, "TIKTOK_BROWSER", browser):
            payload = response_json(main.delete_job_endpoint(request_for(self.user_a, "DELETE"), "job-a"))
            self.assertTrue(payload["deleted"])
            with self.assertRaises(HTTPException) as missing:
                main.delete_job_endpoint(request_for(self.user_a, "DELETE"), "job-a")
            self.assertEqual(missing.exception.status_code, 404)
        self.assertIsNone(self.service.get("job-a"))
        self.assertEqual((self.output_dir / "users" / "user-a" / "job-a" / "video.mp4").read_bytes(), b"video-a")
        governance.release.assert_called_once_with("job-a")

    def test_admin_view_is_explicit_and_role_protected(self) -> None:
        governance = Mock()
        with patch.object(main, "TENANT_GOVERNANCE", governance):
            with self.assertRaises(HTTPException) as denied:
                main.admin_list_jobs(
                    request_for(self.user_a), user_id=None, status=None, search=None, limit=50, offset=0
                )
            self.assertEqual(denied.exception.status_code, 403)

            payload = response_json(main.admin_list_jobs(
                request_for(self.admin), user_id=None, status=None, search=None, limit=50, offset=0
            ))
        self.assertEqual({item["job_id"] for item in payload["items"]}, {"job-a", "job-b"})
        governance.audit.assert_called_once()

    def test_link_only_batch_keeps_http_request_account(self) -> None:
        signature = inspect.signature(main.create_batch_endpoint)
        values = {}
        for name, parameter in signature.parameters.items():
            if name == "request":
                values[name] = request_for(self.user_a, "POST")
            elif name == "source_links":
                values[name] = "https://youtu.be/dQw4w9WgXcQ"
            else:
                default = parameter.default
                values[name] = getattr(default, "default", default)
        governance = Mock()
        governance.overview.return_value = {"remaining_minutes": 30}
        with patch.object(main, "TENANT_GOVERNANCE", governance), patch.object(main, "notify_scheduler"):
            response = asyncio.run(main.create_batch_endpoint(**values))

        payload = response_json(response)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(payload["total_jobs"], 1)
        job = self.service.get_for_user(payload["jobs"][0]["job_id"], "user-a")
        self.assertEqual(job["source_platform"].lower(), "youtube")


if __name__ == "__main__":
    unittest.main()
