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

    def test_several_library_tracks_play_one_after_another(self) -> None:
        self.wait_for_choice()
        files = {}
        for track_id in ("t1", "t2"):
            files[track_id] = self.upload_dir / f"{track_id}.mp3"
            files[track_id].write_bytes(track_id.encode())
        library = Mock()
        library.get.side_effect = lambda track_id: {"track_id": track_id}
        library.file_path.side_effect = lambda track: files[track["track_id"]]
        seen = {}

        def fake_join(ffmpeg, tracks, output):
            seen["tracks"] = list(tracks)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"joined")
            return output

        def fake_render(ffmpeg, images, audio, output, seconds, *, work_dir):
            seen["audio"] = audio
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"slideshow")
            return seconds

        with patch.object(main, "MUSIC_LIBRARY", library), \
                patch.object(main, "join_audio_tracks", side_effect=fake_join), \
                patch.object(main, "render_timed_slideshow", side_effect=fake_render):
            main.choose_photo_post_endpoint(request_for(account("user-a")), "job-p",
                                            main.PhotoPostChoice(action="video", minutes=1,
                                                                 music_track_ids=["t2", "t1"]))
            self.run_worker()
        job = self.service.get("job-p")
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertEqual(job["photo_choice"]["music_track_ids"], ["t2", "t1"])
        self.assertEqual(seen["tracks"], [files["t2"], files["t1"]])
        self.assertEqual(seen["audio"].name, "music.m4a")

    def test_ai_plan_is_validated_stored_and_sent_to_the_planned_renderer(self) -> None:
        self.wait_for_choice()
        plan = {
            "version": 1, "source": "fake", "style": "cinematic", "mood": "ấm áp",
            "summary": "Mở nhẹ và kết chậm", "confidence": 0.8, "keep_original_order": False,
            "slides": [
                {"image_index": 2, "duration_seconds": 5, "motion": "slow_zoom_in",
                 "transition": "crossfade", "transition_seconds": 0.4, "role": "hook"},
                {"image_index": 0, "duration_seconds": 5, "motion": "pan_right",
                 "transition": "fade", "transition_seconds": 0.4, "role": "story"},
                {"image_index": 1, "duration_seconds": 5, "motion": "slow_zoom_out",
                 "transition": "cut", "transition_seconds": 0, "role": "ending"},
            ],
            "music_suggestions": [],
        }
        library = Mock()
        library.list.return_value = []
        seen = {}

        def fake_render(ffmpeg, images, validated_plan, audio, output, *, work_dir):
            seen.update(images=list(images), plan=validated_plan, audio=audio)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"planned-slideshow")
            return sum(slide["duration_seconds"] for slide in validated_plan["slides"])

        with patch.object(main, "MUSIC_LIBRARY", library), \
                patch.object(main, "render_planned_slideshow", side_effect=fake_render):
            main.choose_photo_post_endpoint(
                request_for(account("user-a")), "job-p",
                main.PhotoPostChoice(action="video", minutes=0.25, slideshow_plan=plan),
            )
            self.run_worker()
        job = self.service.get("job-p")
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertEqual([slide["image_index"] for slide in seen["plan"]["slides"]], [2, 0, 1])
        self.assertEqual(seen["audio"], self.music)
        self.assertEqual(job["photo_choice"]["slideshow_plan"]["version"], 1)

    def test_ai_suggests_library_music_from_the_post(self) -> None:
        self.wait_for_choice()
        stored = self.service.get("job-p")["resume_request"]
        stored["photo_post"]["title"] = "Chia tay mùa hạ #sad"
        self.service.update("job-p", resume_request=stored)
        library = Mock()
        library.list.return_value = [
            {"track_id": "t1", "title": "Mưa", "category": "Buồn / Cảm động", "duration_seconds": 60.0},
            {"track_id": "t2", "title": "Party", "category": "Sôi động / EDM", "duration_seconds": 60.0},
        ]
        model = Mock(name="model", accepts_images=False, timeout=900.0)
        model.name = "fake-model"
        model.generate_json.return_value = json.dumps(
            {"mood": "buồn", "suggestions": [{"track_id": "t1", "reason": "Giai điệu buồn"}]})
        with patch.object(main, "MUSIC_LIBRARY", library), \
                patch.object(main, "create_story_llm", return_value=model):
            result = body(main.suggest_photo_post_music_endpoint(request_for(account("user-a")), "job-p"))
        self.assertEqual(result["source"], "fake-model")
        self.assertEqual([item["track_id"] for item in result["items"]], ["t1"])
        self.assertIn("Chia tay mùa hạ #sad", model.generate_json.call_args.args[1])
        self.assertEqual(model.timeout, 120.0)

        with patch.object(main, "MUSIC_LIBRARY", library), \
                patch.object(main, "create_story_llm", side_effect=main.LLMError("không có model")):
            result = body(main.suggest_photo_post_music_endpoint(request_for(account("user-a")), "job-p"))
        self.assertEqual((result["source"], [item["track_id"] for item in result["items"]]), ("heuristic", ["t1"]))

        with self.assertRaises(HTTPException) as other_user:
            main.suggest_photo_post_music_endpoint(request_for(account("user-b")), "job-p")
        self.assertEqual(other_user.exception.status_code, 404)

    def test_ai_director_returns_validated_plan_and_hides_other_users_job(self) -> None:
        self.wait_for_choice()
        library = Mock()
        library.list.return_value = [
            {"track_id": "t1", "title": "Travel", "category": "Du lịch", "duration_seconds": 60.0},
        ]
        model = Mock(accepts_images=False, timeout=900.0)
        model.name = "director-model"
        model.generate_json.return_value = json.dumps({
            "style": "travel", "mood": "khám phá", "summary": "Mở bằng ảnh nổi bật", "confidence": 0.9,
            "slides": [
                {"image_index": 2, "duration_seconds": 3, "motion": "pan_right", "transition": "crossfade",
                 "transition_seconds": 0.4, "role": "hook"},
                {"image_index": 0, "duration_seconds": 3, "motion": "slow_zoom_in", "transition": "fade",
                 "transition_seconds": 0.4, "role": "story"},
                {"image_index": 1, "duration_seconds": 3, "motion": "static", "transition": "cut",
                 "transition_seconds": 0, "role": "ending"},
            ],
            "music_suggestions": [
                {"track_id": "t1", "reason": "Hợp không khí khám phá", "score": 0.9},
                {"track_id": "invented", "reason": "Bịa", "score": 1},
            ],
        })
        with patch.object(main, "MUSIC_LIBRARY", library), \
                patch.object(main, "create_story_llm", return_value=model):
            result = body(main.create_photo_post_ai_plan_endpoint(
                request_for(account("user-a")), "job-p",
                main.PhotoPostAiPlanRequest(target_minutes=0.25, keep_original_order=False),
            ))
        self.assertEqual(result["source"], "director-model")
        self.assertEqual([slide["image_index"] for slide in result["slides"]], [2, 0, 1])
        self.assertEqual([item["track_id"] for item in result["music_suggestions"]], ["t1"])
        self.assertEqual(model.timeout, 120.0)

        with self.assertRaises(HTTPException) as other_user:
            main.create_photo_post_ai_plan_endpoint(
                request_for(account("user-b")), "job-p", main.PhotoPostAiPlanRequest(),
            )
        self.assertEqual(other_user.exception.status_code, 404)

    def test_playlist_command_joins_tracks_in_order(self) -> None:
        from infrastructure.tiktok_slideshow import build_audio_playlist_command, join_audio_tracks

        command = build_audio_playlist_command("ffmpeg", [Path("a.mp3"), Path("b.wav")], Path("out.m4a"))
        self.assertEqual([command[i + 1] for i, part in enumerate(command) if part == "-i"], ["a.mp3", "b.wav"])
        self.assertIn("[a0][a1]concat=n=2:v=0:a=1[audio]", command[command.index("-filter_complex") + 1])
        self.assertEqual(join_audio_tracks("ffmpeg", [Path("only.mp3")], Path("out.m4a")), Path("only.mp3"))

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
