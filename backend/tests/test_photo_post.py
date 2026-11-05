"""Photo posts: the job waits for the owner, then zips the images or renders a slideshow."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request

import main
from application.job_service import JobService
from infrastructure.job_store import SQLiteJobStore
from infrastructure.push_notifications import job_message


def account(user_id: str) -> dict[str, str]:
    return {"user_id": user_id, "email": f"{user_id}@test.local", "display_name": user_id,
            "role": "editor", "plan": "free", "approval_status": "approved"}


def request_for(value: dict[str, str]) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.account = value
    return request


def body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


class PhotoPostJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.output_dir, self.upload_dir, self.work_dir = root / "outputs", root / "uploads", root / "work"
        for directory in (self.output_dir, self.upload_dir, self.work_dir):
            directory.mkdir()
        photos = self.upload_dir / "job-p.photos"
        photos.mkdir()
        self.images = []
        for number in range(3):
            image = photos / f"image_{number:02d}.jpg"
            image.write_bytes(f"image-{number}".encode())
            self.images.append(image)
        self.music = photos / "music.mp3"
        self.music.write_bytes(b"music")
        preview = self.upload_dir / "job-p.mp4"
        preview.write_bytes(b"preview")
        self.service = JobService(SQLiteJobStore(root / "jobs.sqlite3"))
        self.request = {
            "mode": 1, "video_path": str(preview), "source_url": "https://www.tiktok.com/@a/photo/1",
            "apply_logo": False,
            "photo_post": {"images": [str(path) for path in self.images], "music": str(self.music)},
        }
        self.service.create("job-p", {"status": "processing", "filename": "Ảnh.mp4", "resume_request": self.request},
                            owner_user_id="user-a")
        self.governance = Mock()
        for active in (
            patch.object(main, "JOB_SERVICE", self.service),
            patch.object(main, "OUTPUT_DIR", self.output_dir),
            patch.object(main, "UPLOAD_DIR", self.upload_dir),
            patch.object(main, "WORK_DIR", self.work_dir),
            patch.object(main, "TENANT_GOVERNANCE", self.governance),
            patch.object(main, "validate_video_file"),
            patch.object(main, "reserve_job_quota"),
            patch.object(main, "notify_scheduler"),
        ):
            active.start()
            self.addCleanup(active.stop)

    def run_worker(self) -> None:
        """What the scheduler does with a claimed job."""
        job = self.service.get("job-p")
        run_request = dict(job["resume_request"])
        if job.get("job_action"):
            run_request["_job_action"] = job["job_action"]
        main.resume_process_video("job-p", run_request)

    def wait_for_choice(self) -> None:
        main.resume_process_video("job-p", dict(self.request))
        job = self.service.get("job-p")
        self.assertEqual(job["status"], "awaiting_input", job.get("error"))
        self.assertEqual(job["photo_post"], {"image_count": 3, "has_music": True})
        self.governance.release.assert_called_once_with("job-p")

    def test_the_job_stops_and_asks_the_owner(self) -> None:
        self.wait_for_choice()
        title, text, data = job_message("job-p", "awaiting_input", self.service.get("job-p"))
        self.assertEqual(title, "Bài đăng ảnh: chọn cách xử lý")
        self.assertIn("3 ảnh", text)
        self.assertEqual(data["status"], "awaiting_input")

    def test_images_choice_hands_back_a_zip(self) -> None:
        self.wait_for_choice()
        queued = body(main.choose_photo_post_endpoint(
            request_for(account("user-a")), "job-p", main.PhotoPostChoice(action="images")))
        self.assertEqual(queued["status"], "queued")
        self.run_worker()
        job = self.service.get("job-p")
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertIsNone(job["job_action"])
        archive = self.output_dir / job["images_archive"]
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(bundle.namelist(), ["anh_01.jpg", "anh_02.jpg", "anh_03.jpg"])
            self.assertEqual(bundle.read("anh_02.jpg"), b"image-1")
        self.assertEqual(main.job_response("job-p", job)["images_url"], "/api/v1/jobs/job-p/artifacts/images")
        self.governance.consume.assert_called_once_with("job-p")

    def test_video_choice_renders_the_chosen_length_with_the_post_music(self) -> None:
        self.wait_for_choice()
        rendered = {}

        def fake_render(ffmpeg, images, audio, output, seconds, *, work_dir):
            rendered.update(images=list(images), audio=audio, seconds=seconds)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"slideshow")
            return seconds

        main.choose_photo_post_endpoint(
            request_for(account("user-a")), "job-p", main.PhotoPostChoice(action="video", minutes=2.5))
        main.reserve_job_quota.assert_called_once()
        self.assertEqual(main.reserve_job_quota.call_args.kwargs["minutes"], 3)
        with patch.object(main, "render_timed_slideshow", side_effect=fake_render):
            self.run_worker()
        job = self.service.get("job-p")
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertEqual(rendered, {"images": self.images, "audio": self.music, "seconds": 150.0})
        self.assertEqual((self.output_dir / job["output_video"]).read_bytes(), b"slideshow")
        self.assertIn("2 phút 30 giây", job["step_detail"])

    def test_a_library_track_replaces_the_post_music(self) -> None:
        self.wait_for_choice()
        track_file = self.upload_dir / "track.mp3"
        track_file.write_bytes(b"track")
        library = Mock()
        library.get.return_value = {"track_id": "t1"}
        library.file_path.return_value = track_file
        seen = {}

        def fake_render(ffmpeg, images, audio, output, seconds, *, work_dir):
            seen["audio"] = audio
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"slideshow")
            return seconds

        with patch.object(main, "MUSIC_LIBRARY", library), \
                patch.object(main, "render_timed_slideshow", side_effect=fake_render):
            main.choose_photo_post_endpoint(request_for(account("user-a")), "job-p",
                                            main.PhotoPostChoice(action="video", minutes=1, music_track_id="t1"))
            self.run_worker()
        self.assertEqual(seen["audio"], track_file)

    def test_invalid_choices_are_refused(self) -> None:
        with self.assertRaises(HTTPException) as not_waiting:
            main.choose_photo_post_endpoint(request_for(account("user-a")), "job-p",
                                            main.PhotoPostChoice(action="images"))
        self.assertEqual(not_waiting.exception.status_code, 409)
        self.wait_for_choice()
        for minutes in (None, 0.05, 11):
            with self.assertRaises(HTTPException) as refused:
                main.choose_photo_post_endpoint(request_for(account("user-a")), "job-p",
                                                main.PhotoPostChoice(action="video", minutes=minutes))
            self.assertEqual(refused.exception.status_code, 400, minutes)
        with self.assertRaises(HTTPException) as hidden:
            main.choose_photo_post_endpoint(request_for(account("user-b")), "job-p",
                                            main.PhotoPostChoice(action="images"))
        self.assertEqual(hidden.exception.status_code, 404)

    def test_a_plan_without_the_feature_is_not_asked(self) -> None:
        accounts = Mock()
        accounts.get.return_value = {**account("user-a"), "plan": "free"}
        with patch.object(main, "ADMIN_ACCOUNTS", accounts), \
                patch.object(main, "feature_allowed", return_value=False):
            main.resume_process_video("job-p", dict(self.request))
        job = self.service.get("job-p")
        self.assertEqual(job["status"], "failed")
        self.assertIn("Xử lý bài đăng ảnh", job["error"])
        self.governance.release.assert_called_with("job-p")

    def test_the_choice_needs_the_plan_feature(self) -> None:
        self.wait_for_choice()
        with patch.object(main, "ensure_feature_allowed",
                          side_effect=main.PlanEntitlementError("Xử lý bài đăng ảnh chỉ dành cho...")):
            with self.assertRaises(HTTPException) as refused:
                main.choose_photo_post_endpoint(request_for(account("user-a")), "job-p",
                                                main.PhotoPostChoice(action="images"))
        self.assertEqual(refused.exception.status_code, 403)
        self.assertEqual(self.service.get("job-p")["status"], "awaiting_input")

    def test_photo_post_is_a_plan_feature_every_plan_starts_with(self) -> None:
        from domain.plan_entitlements import DEFAULT_PLAN_FEATURES, FEATURES

        self.assertIn("photo_post", FEATURES)
        self.assertTrue(all("photo_post" in features for features in DEFAULT_PLAN_FEATURES.values()))

    def test_a_waiting_job_can_be_cancelled_at_once(self) -> None:
        self.wait_for_choice()
        self.assertEqual(self.service.request_cancel("job-p")["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
