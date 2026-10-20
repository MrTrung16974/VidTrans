import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request

import main
from application.job_service import JobService
from infrastructure.job_store import SQLiteJobStore


def request_for(user_id: str, method: str = "POST") -> Request:
    request = Request({"type": "http", "method": method, "path": "/", "headers": []})
    request.state.account = {
        "user_id": user_id,
        "email": f"{user_id}@test.local",
        "display_name": user_id,
        "role": "editor",
        "plan": "free",
        "approval_status": "approved",
    }
    return request


def response_json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


class JobCancellationTests(unittest.TestCase):
    def test_asr_progress_checks_cancellation_before_updating_progress(self) -> None:
        callback = main.make_asr_progress_callback("job-asr", 60.0)

        with patch.object(main, "ensure_job_active", side_effect=main.JobCancelled("stop")), patch.object(
            main, "update_job"
        ) as update:
            with self.assertRaises(main.JobCancelled):
                callback(10.0, 60.0)

        update.assert_not_called()

    def test_ocr_progress_checks_cancellation_even_when_progress_is_throttled(self) -> None:
        callback = main.make_ocr_progress_callback("job-ocr")

        with patch.object(main, "ensure_job_active", side_effect=main.JobCancelled("stop")), patch.object(
            main, "update_job"
        ) as update:
            with self.assertRaises(main.JobCancelled):
                callback(1, 10_000)

        update.assert_not_called()

    def test_ffmpeg_uses_explicit_job_id_and_terminates_on_cancel(self) -> None:
        process = Mock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["ffmpeg"], 0.5),
            ("", "cancelled"),
        ]
        process.returncode = -15

        with patch.object(main.subprocess, "Popen", return_value=process), patch.object(
            main.JOB_SERVICE, "is_cancel_requested", return_value=True
        ) as is_cancel_requested:
            with self.assertRaises(main.JobCancelled):
                main.run_ffmpeg(["ffmpeg", "-version"], job_id="job-ffmpeg")

        is_cancel_requested.assert_called_once_with("job-ffmpeg")
        process.terminate.assert_called_once_with()
        process.kill.assert_not_called()

    def test_tts_does_not_start_new_cue_after_cancel(self) -> None:
        segments = [{"text": "xin chào", "start": 0.0, "end": 1.0} for _ in range(3)]

        with tempfile.TemporaryDirectory() as work_dir, patch.object(
            main, "ensure_job_active", side_effect=main.JobCancelled("stop")
        ), patch.object(main, "tts_edge_with_retry") as tts, patch.object(main, "run_ffmpeg") as ffmpeg:
            with self.assertRaises(main.JobCancelled):
                main.synthesize_tts_segments(
                    segments, Path(work_dir), "vi-VN-HoaiMyNeural", 1.0, job_id="job-tts"
                )

        tts.assert_not_called()
        ffmpeg.assert_not_called()


class CancellationLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.output_dir = root / "outputs"
        self.upload_dir = root / "uploads"
        self.work_dir = root / "work"
        for directory in (self.output_dir, self.upload_dir, self.work_dir):
            directory.mkdir()
        self.source_video = self.upload_dir / "source.mp4"
        self.source_video.write_bytes(b"video")
        self.service = JobService(SQLiteJobStore(root / "jobs.sqlite3"))
        self.governance = Mock()
        browser = Mock()
        browser.active_attempt.return_value = None
        self.patches = [
            patch.object(main, "JOB_SERVICE", self.service),
            patch.object(main, "OUTPUT_DIR", self.output_dir),
            patch.object(main, "UPLOAD_DIR", self.upload_dir),
            patch.object(main, "WORK_DIR", self.work_dir),
            patch.object(main, "TENANT_GOVERNANCE", self.governance),
            patch.object(main, "TIKTOK_BROWSER", browser),
            patch.object(main, "notify_scheduler"),
            patch.object(main, "N8N_WORKFLOW_CONFIG", Mock(enabled=False)),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp.cleanup()

    def create_running_job(self, job_id: str = "job-1", **payload) -> None:
        self.service.create(
            job_id,
            {
                "status": "queued",
                "resume_request": {"video_path": str(self.source_video)},
                **payload,
            },
            owner_user_id="user-a",
        )
        self.service.claim_next("worker")

    def test_cancel_running_job_keeps_quota_and_blocks_retry_and_delete(self) -> None:
        self.create_running_job()

        payload = response_json(main.cancel_job_endpoint(request_for("user-a"), "job-1"))

        self.assertEqual(payload["status"], "cancelling")
        self.governance.release.assert_not_called()
        for operation in (
            lambda: main.retry_job_endpoint(request_for("user-a"), "job-1"),
            lambda: main.delete_job_endpoint(request_for("user-a", "DELETE"), "job-1"),
        ):
            with self.assertRaises(HTTPException) as conflict:
                operation()
            self.assertEqual(conflict.exception.status_code, 409)
        self.assertTrue(self.source_video.exists())
        self.assertEqual(self.service.get("job-1")["status"], "cancelling")
        self.governance.release.assert_not_called()

    def test_cancel_queued_job_releases_quota_immediately(self) -> None:
        self.service.create("queued", {"status": "queued"}, owner_user_id="user-a")

        payload = response_json(main.cancel_job_endpoint(request_for("user-a"), "queued"))

        self.assertEqual(payload["status"], "cancelled")
        self.governance.release.assert_called_once_with("queued")

    def test_cancel_by_other_tenant_is_not_found(self) -> None:
        self.create_running_job()

        with self.assertRaises(HTTPException) as denied:
            main.cancel_job_endpoint(request_for("user-b"), "job-1")

        self.assertEqual(denied.exception.status_code, 404)
        self.assertEqual(self.service.get("job-1")["status"], "processing")

    def test_worker_ack_then_retry_requeues_without_cancel_flag(self) -> None:
        self.create_running_job()
        self.service.request_cancel("job-1")

        main.update_job("job-1", status="cancelled", step="cancelled", finished_at=main.utc_now())
        with patch.object(main, "reserve_job_quota") as reserve:
            payload = response_json(main.retry_job_endpoint(request_for("user-a"), "job-1"))

        reserve.assert_called_once()
        self.assertEqual(payload["status"], "queued")
        self.assertFalse(self.service.is_cancel_requested("job-1"))

    def test_late_failure_while_cancelling_finishes_as_cancelled(self) -> None:
        self.create_running_job()
        self.service.request_cancel("job-1")

        main.update_job("job-1", status="failed", step="failed", error="boom", finished_at=main.utc_now())

        job = self.service.get("job-1")
        self.assertEqual(job["status"], "cancelled")
        self.assertIsNotNone(job["finished_at"])

    def test_late_completion_while_cancelling_is_rejected(self) -> None:
        self.create_running_job()
        self.service.request_cancel("job-1")

        with self.assertRaises(main.JobCancelled):
            main.update_job("job-1", status="completed", progress=1.0)

        self.assertEqual(self.service.get("job-1")["status"], "cancelling")

    def test_scheduled_publish_cancelled_before_upload_does_not_call_tiktok(self) -> None:
        self.create_running_job()
        self.service.request_cancel("job-1")
        publisher_factory = Mock()

        with patch.object(main, "tenant_tiktok_publisher", publisher_factory):
            main.publish_scheduled_tiktok("job-1", {"video_path": "out.mp4", "title": "Tiêu đề"})

        publisher_factory.assert_not_called()
        self.assertEqual(self.service.get("job-1")["status"], "cancelled")
        self.governance.release.assert_called_once_with("job-1")

    def test_cancel_during_scheduled_upload_keeps_result_and_warns(self) -> None:
        self.create_running_job()
        publisher = Mock()

        def publish(*_args, **_kwargs):
            self.service.request_cancel("job-1")
            return {"publish_id": "pub-1", "title": "Tiêu đề", "privacy_level": "SELF_ONLY", "status": "PROCESSING"}

        publisher.publish.side_effect = publish
        with patch.object(main, "tenant_tiktok_publisher", return_value=publisher):
            main.publish_scheduled_tiktok("job-1", {"video_path": "out.mp4", "title": "Tiêu đề"})

        job = self.service.get("job-1")
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["tiktok_publish_id"], "pub-1")
        self.assertEqual(job["step_detail"], main.TIKTOK_CANCEL_UNCERTAIN_DETAIL)
        self.governance.consume.assert_called_once_with("job-1")
        self.governance.release.assert_not_called()


if __name__ == "__main__":
    unittest.main()
