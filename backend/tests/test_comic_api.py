import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import main
from application.job_service import JobService
from infrastructure.job_store import SQLiteJobStore

STORY = ("Đêm hôm đó, Minh bước vào khu rừng. Ánh trăng lạnh lẽo xuyên qua tán lá.\n\n"
         "Bỗng một bóng trắng lướt qua. Cô gái tóc dài đứng giữa lối mòn, mỉm cười buồn bã.")


def account(user_id: str, role: str = "editor") -> dict:
    return {"user_id": user_id, "email": f"{user_id}@test.local", "display_name": user_id, "role": role,
            "plan": "free", "approval_status": "approved"}


def fake_speech(text, output_path, gender, rate, pitch, language):
    subprocess.run([shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=0.8",
                    "-ac", "1", str(output_path)], check=True, capture_output=True)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is required")
class ComicApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name in ("outputs", "uploads", "work"):
            (root / name).mkdir()
        self.service = JobService(SQLiteJobStore(root / "jobs.sqlite3"))
        self.users = {"a": account("user-a"), "b": account("user-b")}
        patches = [
            patch.object(main, "JOB_SERVICE", self.service),
            patch.object(main, "OUTPUT_DIR", root / "outputs"),
            patch.object(main, "UPLOAD_DIR", root / "uploads"),
            patch.object(main, "WORK_DIR", root / "work"),
            patch.object(main, "FFMPEG", shutil.which("ffmpeg")),
            patch.object(main, "FFPROBE", shutil.which("ffprobe")),
            patch.object(main, "TENANT_GOVERNANCE", Mock()),
            patch.object(main, "reserve_job_quota", Mock()),
            patch.object(main, "synthesize_comic_speech", fake_speech),
            patch.object(main, "current_account",
                         lambda request: self.users[request.headers.get("x-test-user", "a")]),
        ]
        for active in patches:
            active.start()
            self.addCleanup(active.stop)
        self.client = TestClient(main.app)

    def create(self, **form):
        data = {"title": "Rừng đêm", "text": STORY, "llm_provider": "heuristic", "image_provider": "placeholder",
                "aspect_ratio": "16:9", "target_scene_duration": "4", **form}
        return self.client.post("/api/v1/comic/projects", data=data)

    def run_job(self, job_id: str) -> dict:
        job = self.service.get(job_id)
        main.resume_process_video(job_id, {**job["resume_request"], "_job_action": job["job_action"]})
        return self.service.get(job_id)

    def project(self, job_id: str, user: str = "a"):
        return self.client.get(f"/api/v1/comic/projects/{job_id}", headers={"x-test-user": user})

    def test_full_flow_create_render_edit_and_rerender(self) -> None:
        created = self.create()
        self.assertEqual(created.status_code, 202, created.text)
        job_id = created.json()["job_id"]
        self.assertEqual(self.service.get(job_id)["job_action"], "comic_video")

        # Editing is refused while the job is queued.
        queued_edit = self.client.patch(f"/api/v1/comic/projects/{job_id}/scenes/1", json={"narration": "x"})
        self.assertEqual(queued_edit.status_code, 409)

        job = self.run_job(job_id)
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertTrue(job["output_video"].endswith(f"comic_{job_id}.mp4"))
        video = self.client.get(f"/api/v1/jobs/{job_id}/artifacts/video")
        self.assertEqual(video.status_code, 200)

        payload = self.project(job_id).json()
        board = payload["storyboard"]
        self.assertGreaterEqual(len(board["scenes"]), 2)
        image_url = payload["media"]["scenes"]["1"]
        image = self.client.get(image_url)
        self.assertEqual((image.status_code, image.headers["content-type"]), (200, "image/png"))

        edited = self.client.patch(f"/api/v1/comic/projects/{job_id}/scenes/1",
                                   json={"narration": "Minh run rẩy bước vào rừng.", "camera_motion": "pan_right"})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["storyboard"]["revision"], 2)
        self.assertEqual(edited.json()["storyboard"]["scenes"][0]["version"], 2)

        rerender = self.client.post(f"/api/v1/comic/projects/{job_id}/render", json={})
        self.assertEqual(rerender.status_code, 202, rerender.text)
        job = self.run_job(job_id)
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertEqual(job["comic"]["rendered_revision"], 2)
        self.assertGreater(job["comic"]["cache"]["hits"], 0)

    def test_cit_voices_are_resolved_and_stored_with_the_project(self) -> None:
        from infrastructure.tts_providers import CITVoice

        voices = [CITVoice("Ngọc Huyền", "Ngọc Huyền", "female"), CITVoice("Thu Trang", "Thu Trang", "female"),
                  CITVoice("Minh Đức", "Minh Đức", "male")]
        with patch.dict("os.environ", {"VIDTRANS_CIT_VOICE_URL": "http://cit.local:8001"}), \
                patch("infrastructure.tts_providers.cit_voices", return_value=voices):
            options = self.client.get("/api/v1/comic/options").json()
            created = self.create(tts_provider="cit", narrator_voice="female",
                                  cit_narrator_voice="Thu Trang", cit_female_voice="", cit_male_voice="Minh Đức")
            rejected = self.create(tts_provider="cit", cit_male_voice="Đã xoá")

        cit = next(item for item in options["tts_providers"] if item["key"] == "cit")
        self.assertTrue(cit["configured"])
        self.assertEqual(created.status_code, 202, created.text)
        settings = self.service.get(created.json()["job_id"])["resume_request"]["settings"]
        self.assertEqual(settings["tts_provider"], "cit")
        self.assertEqual(settings["tts_voices"], {"narrator": "Thu Trang", "female": "Ngọc Huyền", "male": "Minh Đức"})
        self.assertEqual(rejected.status_code, 400)

    @patch.dict("os.environ", {}, clear=True)
    def test_cit_requires_the_app(self) -> None:
        created = self.create(tts_provider="cit")
        self.assertEqual(created.status_code, 400)
        self.assertIn("CIT Voice Studio", created.json()["detail"])

    def test_validation_errors_are_rejected(self) -> None:
        self.assertEqual(self.create(text="   ").status_code, 400)
        self.assertEqual(self.create(style="pixel_art").status_code, 400)
        self.assertEqual(self.create(text="x" * 60_001).status_code, 400)
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            self.assertEqual(self.create(image_provider="gemini").status_code, 400)
        with patch.dict("os.environ", {"VIDTRANS_COMFYUI_URL": "", "VIDTRANS_COMFYUI_CHECKPOINT": ""}):
            self.assertEqual(self.create(image_provider="comfyui").status_code, 400)
        upload = self.client.post("/api/v1/comic/projects", data={"llm_provider": "heuristic"},
                                  files={"file": ("story.pdf", b"%PDF-1.4", "application/pdf")})
        self.assertEqual(upload.status_code, 400)

    def test_docx_upload_and_images_only_preview(self) -> None:
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml",
                             '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                             "<w:body><w:p><w:r><w:t>Minh đi học. Lan đợi ở cổng.</w:t></w:r></w:p></w:body></w:document>")
        created = self.client.post(
            "/api/v1/comic/projects",
            data={"llm_provider": "heuristic", "image_provider": "placeholder", "auto_render": "false"},
            files={"file": ("story.docx", buffer.getvalue(),
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        self.assertEqual(created.status_code, 202, created.text)
        job = self.run_job(created.json()["job_id"])
        self.assertEqual((job["status"], job["step"]), ("completed", "storyboard_ready"))
        self.assertIsNone(job.get("output_video"))
        story = self.client.get(f"/api/v1/comic/projects/{created.json()['job_id']}/story").json()["text"]
        self.assertEqual(story, "Minh đi học. Lan đợi ở cổng.")

    def test_scene_edits_are_validated_and_owner_scoped(self) -> None:
        job_id = self.create(auto_render="false").json()["job_id"]
        self.run_job(job_id)
        base = f"/api/v1/comic/projects/{job_id}"

        self.assertEqual(self.project(job_id, "b").status_code, 404)
        self.assertEqual(self.client.patch(f"{base}/scenes/1", json={"narration": "x"},
                                           headers={"x-test-user": "b"}).status_code, 404)
        self.assertEqual(self.client.get(f"{base}/media/images/..%2Fstory.txt").status_code, 404)

        no_speech = self.client.patch(f"{base}/scenes/1", json={"narration": ""})
        self.assertEqual(no_speech.status_code, 422)
        stranger = self.client.patch(f"{base}/scenes/1", json={"characters": ["ghost"]})
        self.assertEqual(stranger.status_code, 422)
        self.assertEqual(self.client.patch(f"{base}/scenes/1", json={"bogus": 1}).status_code, 422)
        self.assertEqual(self.client.patch(f"{base}/scenes/99", json={"narration": "x"}).status_code, 404)

        inserted = self.client.post(f"{base}/scenes", json={"after": 0, "narration": "Mở đầu.",
                                                             "visual_prompt": "Title card of a forest"})
        self.assertEqual(inserted.json()["storyboard"]["scenes"][0]["narration"], "Mở đầu.")
        count = len(inserted.json()["storyboard"]["scenes"])
        deleted = self.client.delete(f"{base}/scenes/1")
        self.assertEqual(len(deleted.json()["storyboard"]["scenes"]), count - 1)

    def test_uploaded_scene_image_and_generic_retry_keep_the_project(self) -> None:
        from PIL import Image

        job_id = self.create(auto_render="false").json()["job_id"]
        self.run_job(job_id)
        base = f"/api/v1/comic/projects/{job_id}"
        bad = self.client.post(f"{base}/scenes/1/image", files={"file": ("x.png", b"nope", "image/png")})
        self.assertEqual(bad.status_code, 400)
        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), (0, 120, 0)).save(buffer, format="JPEG")
        uploaded = self.client.post(f"{base}/scenes/1/image", files={"file": ("x.jpg", buffer.getvalue(), "image/jpeg")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        override = uploaded.json()["storyboard"]["scenes"][0]["image_override"]
        self.assertTrue(override.startswith("s001-"))
        self.assertEqual(self.client.get(uploaded.json()["media"]["uploads"]["1"]).status_code, 200)

        retried = self.client.post(f"/api/v1/jobs/{job_id}/retry")
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(self.service.get(job_id)["status"], "queued")
        job = self.run_job(job_id)
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertEqual(self.project(job_id).json()["storyboard"]["scenes"][0]["image_override"], override)

        cleared = self.client.delete(f"{base}/scenes/1/image")
        self.assertIsNone(cleared.json()["storyboard"]["scenes"][0]["image_override"])

    def test_options_list_styles_and_providers(self) -> None:
        options = self.client.get("/api/v1/comic/options").json()
        self.assertIn("anime_comic", [s["key"] for s in options["styles"]])
        self.assertEqual(options["aspect_ratios"], ["9:16", "16:9"])
        self.assertTrue(next(p for p in options["image_providers"] if p["key"] == "placeholder")["configured"])
        self.assertIn("comfyui", [p["key"] for p in options["image_providers"]])


if __name__ == "__main__":
    unittest.main()
