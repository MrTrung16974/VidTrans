import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import main
from domain.comic import ComicCharacter
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
        self.assertEqual(board["youtube_title"], "Rừng đêm")
        self.assertEqual(job["comic"]["youtube_title"], "Rừng đêm")
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

    def test_story_brief_is_stored_and_planned_places_can_be_fixed(self) -> None:
        from domain.comic import ComicLocation
        from infrastructure.comic_project import ComicProject

        created = self.create(story_brief="Thời nhà Đường. Rừng trúc: lối đá rêu phong.")
        job_id = created.json()["job_id"]
        settings = self.service.get(job_id)["resume_request"]["settings"]
        self.assertEqual(settings["story_brief"], "Thời nhà Đường. Rừng trúc: lối đá rêu phong.")
        job = self.run_job(job_id)
        self.assertEqual(job["status"], "completed", job.get("error"))

        project = ComicProject(main.comic_project_root("user-a", job_id))
        storyboard = project.load_storyboard()
        storyboard.locations = [ComicLocation(id="forest", name="Rừng trúc", description="bamboo forest")]
        storyboard.scenes[0].location_id = "forest"
        project.save_storyboard(storyboard)

        fixed = self.client.patch(f"/api/v1/comic/projects/{job_id}/locations/forest",
                                  json={"description": "dense bamboo forest, mossy stone path"})
        self.assertEqual(fixed.status_code, 200, fixed.text)
        self.assertEqual(fixed.json()["storyboard"]["locations"][0]["description"], "dense bamboo forest, mossy stone path")
        self.assertEqual(self.client.patch(f"/api/v1/comic/projects/{job_id}/locations/moon",
                                           json={"description": "x"}).status_code, 404)
        self.assertEqual(self.client.patch(f"/api/v1/comic/projects/{job_id}/scenes/1",
                                           json={"location_id": "moon"}).status_code, 422)
        # Another account cannot touch it.
        other = self.client.patch(f"/api/v1/comic/projects/{job_id}/locations/forest",
                                  json={"description": "x"}, headers={"x-test-user": "b"})
        self.assertEqual(other.status_code, 404)

    def test_bible_suggestion_marks_new_entries_and_creation_seeds_the_series(self) -> None:
        import json as jsonlib

        answer = jsonlib.dumps({
            "world": "Tang dynasty China", "world_vi": "Trung Quốc thời Đường",
            "characters": [{"id": "minh", "name": "Minh", "gender": "male", "species": "human",
                            "appearance": "young scholar", "appearance_vi": "thư sinh trẻ"}],
            "locations": [{"id": "forest", "name": "Rừng", "description": "forest", "description_vi": "rừng"}],
        })
        fake = Mock()
        fake.generate_json.return_value = answer
        with patch("application.comic_routes.create_story_llm", return_value=fake):
            response = self.client.post("/api/v1/comic/bible/suggest", data={"text": STORY, "genre": "co_trang",
                                                                             "llm_provider": "gemini"})
        self.assertEqual(response.status_code, 200, response.text)
        bible = response.json()
        self.assertEqual(bible["characters"][0]["status"], "new")
        self.assertEqual(bible["genre"], "co_trang")

        with patch("application.comic_routes.create_story_llm", return_value=None):
            refused = self.client.post("/api/v1/comic/bible/suggest", data={"text": STORY, "llm_provider": "heuristic"})
        self.assertEqual(refused.status_code, 409)

        bible["characters"][0]["appearance_vi"] = "thư sinh tóc bạc"
        bible["characters"][0]["appearance"] = ""
        for item in [*bible["characters"], *bible["locations"]]:
            item.pop("status")
        bible.pop("genre")
        created = self.client.post("/api/v1/comic/series", data={
            "title": "Rừng đêm", "text": STORY, "llm_provider": "heuristic", "image_provider": "placeholder",
            "bible": jsonlib.dumps(bible), "genre": "co_trang", "auto_render": "false"})
        self.assertEqual(created.status_code, 201, created.text)
        series = self.client.get(f"/api/v1/comic/series/{created.json()['series_id']}").json()
        self.assertEqual(series["characters"][0]["appearance_vi"], "thư sinh tóc bạc")
        self.assertEqual(series["world_vi"], "Trung Quốc thời Đường")
        self.assertNotIn("bible", series["settings"])
        settings = self.service.get(created.json()["job_id"])["resume_request"]["settings"]
        self.assertEqual(settings["bible"]["characters"][0]["id"], "minh")

        with patch("application.comic_routes.create_story_llm", return_value=fake):
            episode = self.client.post("/api/v1/comic/bible/suggest", data={
                "text": STORY, "llm_provider": "gemini", "series_id": created.json()["series_id"]})
        self.assertEqual(episode.json()["characters"][0]["status"], "known")

        bad = self.client.post("/api/v1/comic/series", data={"title": "x", "bible": "{not json"})
        self.assertEqual(bad.status_code, 400)

    def test_editing_a_look_in_vietnamese_clears_its_english_for_retranslation(self) -> None:
        from application.comic_routes import retranslate_on_edit

        current = {"appearance": "old", "appearance_vi": "cũ", "description": "x", "description_vi": "y"}
        self.assertEqual(retranslate_on_edit(current, {"appearance_vi": "mới"}), {"appearance_vi": "mới", "appearance": ""})
        self.assertEqual(retranslate_on_edit(current, {"appearance_vi": "cũ"}), {"appearance_vi": "cũ"})
        self.assertEqual(retranslate_on_edit(current, {"appearance_vi": "mới", "appearance": "new"}),
                         {"appearance_vi": "mới", "appearance": "new"})

    def test_place_pictures_can_be_uploaded_pinned_and_trial_renders_queue_few_scenes(self) -> None:
        from domain.comic import ComicLocation
        from infrastructure.comic_project import ComicProject

        job_id = self.create(auto_render="false").json()["job_id"]
        self.assertEqual(self.run_job(job_id)["status"], "completed")
        project = ComicProject(main.comic_project_root("user-a", job_id))
        storyboard = project.load_storyboard()
        storyboard.locations = [ComicLocation(id="forest", name="Rừng", description="bamboo forest")]
        storyboard.scenes[0].location_id = "forest"
        project.save_storyboard(storyboard)

        png = io.BytesIO()
        __import__("PIL.Image", fromlist=["Image"]).new("RGB", (32, 32), "green").save(png, "PNG")
        uploaded = self.client.post(f"/api/v1/comic/projects/{job_id}/locations/forest/references",
                                    files={"file": ("forest.png", png.getvalue(), "image/png")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        name = uploaded.json()["storyboard"]["locations"][0]["approved_reference"]
        self.assertTrue(name.startswith("place-forest-upload-"))
        self.assertIn("forest", uploaded.json()["media"]["location_references"])
        self.assertFalse(uploaded.json()["media"]["place_references_supported"])  # Placeholder images.
        variants = self.client.get(f"/api/v1/comic/projects/{job_id}/locations/forest/references").json()
        self.assertEqual([v["name"] for v in variants["variants"]], [name])
        unpinned = self.client.delete(f"/api/v1/comic/projects/{job_id}/locations/forest/references/approval")
        self.assertIsNone(unpinned.json()["storyboard"]["locations"][0]["approved_reference"])
        approved = self.client.post(f"/api/v1/comic/projects/{job_id}/locations/forest/references/{name}/approve")
        self.assertEqual(approved.json()["storyboard"]["locations"][0]["approved_reference"], name)
        self.assertEqual(self.client.post(
            f"/api/v1/comic/projects/{job_id}/locations/forest/references/minh-x.png/approve").status_code, 404)

        trial = self.client.post(f"/api/v1/comic/projects/{job_id}/render",
                                 json={"images_only": True, "preview_scenes": 3})
        self.assertEqual(trial.status_code, 202, trial.text)
        self.assertEqual(self.service.get(job_id)["resume_request"]["preview_scenes"], 3)
        job = self.run_job(job_id)
        self.assertIn("Đã vẽ thử", job["step_detail"])

    def test_quick_fixes_correct_the_scene_and_redraw_only_it(self) -> None:
        from domain.comic import ComicLocation
        from infrastructure.comic_project import ComicProject

        job_id = self.create(auto_render="false").json()["job_id"]
        self.assertEqual(self.run_job(job_id)["status"], "completed")
        project = ComicProject(main.comic_project_root("user-a", job_id))
        storyboard = project.load_storyboard()
        storyboard.characters = [*storyboard.characters,
                                 __import__("domain.comic", fromlist=["ComicCharacter"]).ComicCharacter(
                                     id="minh", name="Minh", outfit_vi="áo trắng", outfit="white robe")]
        storyboard.locations = [ComicLocation(id="forest", name="Rừng", description="forest")]
        project.save_storyboard(storyboard)

        def images() -> dict:
            # Comic projects have one image per scene; animated ones a keyframe per shot ("1.1").
            media = self.project(job_id).json()["media"]
            return media["scenes"] or {label.split(".")[0]: url for label, url in sorted(media["keyframes"].items())
                                       if label.endswith(".1")}

        before = images()

        fixed = self.client.post(f"/api/v1/comic/projects/{job_id}/scenes/1/fix",
                                 json={"kind": "location", "location_id": "forest", "note_vi": "trời mưa"})
        self.assertEqual(fixed.status_code, 200, fixed.text)
        scene = fixed.json()["storyboard"]["scenes"][0]
        self.assertEqual((scene["location_id"], scene["take"], scene["fix_note_vi"], scene["fix_note"]),
                         ("forest", 2, "trời mưa", ""))
        self.assertEqual(self.service.get(job_id)["resume_request"]["preview_scene_indexes"], [1])
        job = self.run_job(job_id)
        self.assertIn("Đã vẽ lại cảnh 1", job["step_detail"])
        after = images()
        self.assertNotEqual(after["1"], before["1"])  # Redrawn.
        self.assertEqual(after["2"], before["2"])  # Untouched.

        outfit = self.client.post(f"/api/v1/comic/projects/{job_id}/scenes/2/fix", json={
            "kind": "outfit", "character_id": "minh", "outfit_vi": "áo giáp bạc", "apply_to_all": True,
            "draw_now": False})
        minh = next(c for c in outfit.json()["storyboard"]["characters"] if c["id"] == "minh")
        self.assertEqual((minh["outfit_vi"], minh["outfit"]), ("áo giáp bạc", ""))
        self.assertIsNone(outfit.json()["queued"])

        local = self.client.post(f"/api/v1/comic/projects/{job_id}/scenes/2/fix", json={
            "kind": "outfit", "character_id": "minh", "outfit_vi": "áo đỏ", "draw_now": False}).json()
        self.assertIn("Minh mặc áo đỏ", local["storyboard"]["scenes"][1]["fix_note_vi"])
        self.assertEqual(self.client.post(f"/api/v1/comic/projects/{job_id}/scenes/2/fix", json={
            "kind": "characters", "characters": ["ghost"], "draw_now": False}).status_code, 422)

        bulk = self.client.post(f"/api/v1/comic/projects/{job_id}/scenes/bulk",
                                json={"scene_indexes": [1, 2, 99], "location_id": "forest", "redraw": True})
        self.assertEqual(bulk.status_code, 200, bulk.text)
        self.assertEqual(bulk.json()["changed"], [1, 2])
        self.assertTrue(all(s["location_id"] == "forest" for s in bulk.json()["storyboard"]["scenes"][:2]))
        self.assertEqual(self.client.post(f"/api/v1/comic/projects/{job_id}/scenes/bulk",
                                          json={"scene_indexes": [1], "location_id": "moon"}).status_code, 422)

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

    def test_projects_and_scenes_are_paginated_on_the_backend(self) -> None:
        # Non-comic job must not leak into comic projects pagination.
        self.service.create("vid-0001", {"status": "completed", "job_action": "translate_video", "filename": "v.mp4"},
                            owner_user_id="user-a")
        ids = [self.create(title=f"Truyện {idx}", auto_render="false").json()["job_id"] for idx in range(1, 4)]

        page1 = self.client.get("/api/v1/comic/projects?page=1&limit=2").json()
        page2 = self.client.get("/api/v1/comic/projects?page=2&limit=2").json()
        self.assertEqual((page1["total"], page1["page"], page1["limit"], page1["total_pages"]), (3, 1, 2, 2))
        self.assertEqual(len(page1["items"]), 2)
        self.assertEqual(len(page2["items"]), 1)
        self.assertTrue(all(item["job_action"] == "comic_video" for item in page1["items"] + page2["items"]))

        # Add scenes so we have 4 scenes and verify backend scene pagination.
        job_id = ids[0]
        self.run_job(job_id)
        base = f"/api/v1/comic/projects/{job_id}"
        for idx in range(2):
            self.client.post(f"{base}/scenes", json={"after": idx, "narration": f"Cảnh thêm {idx + 1}.",
                                                     "visual_prompt": f"Extra scene {idx + 1}"})
        s_page1 = self.client.get(f"{base}?scene_page=1&scene_limit=2").json()
        s_page2 = self.client.get(f"{base}?scene_page=2&scene_limit=2").json()
        self.assertGreaterEqual(s_page1["scenes_pagination"]["total_scenes"], 4)
        self.assertEqual(len(s_page1["storyboard"]["scenes"]), 2)
        self.assertEqual([s["scene_index"] for s in s_page1["storyboard"]["scenes"]], [1, 2])
        self.assertEqual([s["scene_index"] for s in s_page2["storyboard"]["scenes"]], [3, 4])

    def test_series_holds_many_episode_videos_with_shared_settings(self) -> None:
        created = self.client.post("/api/v1/comic/series", data={
            "title": "Tiên Nghịch", "description": "Truyện tu tiên", "text": STORY, "style": "xianxia",
            "llm_provider": "heuristic", "image_provider": "placeholder", "aspect_ratio": "16:9",
            "target_scene_duration": "4", "episode_title": "Nhập môn"})
        self.assertEqual(created.status_code, 201, created.text)
        series_id, first = created.json()["series_id"], created.json()["job_id"]
        self.assertEqual(created.json()["episode_number"], 1)
        self.assertEqual(self.run_job(first)["status"], "completed")

        # The next episode only sends its story; style, aspect ratio and providers come from the series.
        second = self.create(series_id=series_id, title="", aspect_ratio="9:16", style="anime_comic",
                             auto_render="false")
        self.assertEqual(second.status_code, 202, second.text)
        self.assertEqual(second.json()["episode_number"], 2)
        job = self.run_job(second.json()["job_id"])
        settings = job["resume_request"]["settings"]
        self.assertEqual((settings["style"], settings["aspect_ratio"], settings["title"]), ("xianxia", "16:9", "Tập 2"))
        self.assertEqual((job["comic"]["series_id"], job["comic"]["episode_number"]), (series_id, 2))

        payload = self.client.get(f"/api/v1/comic/series/{series_id}").json()
        self.assertEqual([e["episode_number"] for e in payload["episode_list"]], [1, 2])
        self.assertEqual(payload["episode_list"][0]["comic"]["title"], "Nhập môn")
        self.assertEqual(payload["next_episode_number"], 3)
        listed = self.client.get("/api/v1/comic/series").json()
        self.assertEqual([(s["series_id"], s["episodes"]) for s in listed["items"]], [(series_id, 2)])

        # Another user can neither see nor extend the series.
        other = {"x-test-user": "b"}
        self.assertEqual(self.client.get(f"/api/v1/comic/series/{series_id}", headers=other).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/comic/projects", headers=other, data={
            "text": STORY, "series_id": series_id}).status_code, 404)

        renamed = self.client.patch(f"/api/v1/comic/series/{series_id}", json={"title": "Tiên Nghịch (bản mới)"})
        self.assertEqual(renamed.json()["title"], "Tiên Nghịch (bản mới)")

        # Deleting the series keeps its episode videos.
        self.assertTrue(self.client.delete(f"/api/v1/comic/series/{series_id}").json()["deleted"])
        self.assertEqual(self.client.get(f"/api/v1/comic/series/{series_id}").status_code, 404)
        self.assertEqual(self.project(first).status_code, 200)

    def test_deleting_a_project_removes_its_files(self) -> None:
        job_id = self.create().json()["job_id"]
        self.assertEqual(self.run_job(job_id)["status"], "completed")
        video = main.stored_artifact_path(self.service.get(job_id)["output_video"])
        rate = subprocess.run([main.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=r_frame_rate", "-of", "csv=p=0", str(video)], capture_output=True, text=True)
        self.assertEqual(rate.stdout.strip(), "24/1")  # cinema frame rate by default
        owner = self.service.get(job_id)["owner_user_id"]
        folders = [main.tenant_storage_path(root, owner, job_id) for root in (main.OUTPUT_DIR, main.WORK_DIR)]
        self.assertTrue(all(folder.is_dir() for folder in folders))
        self.assertEqual(self.client.delete(f"/api/v1/comic/projects/{job_id}", headers={"x-test-user": "b"}).status_code, 404)
        deleted = self.client.delete(f"/api/v1/comic/projects/{job_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertFalse(any(folder.exists() for folder in folders))
        self.assertEqual(self.project(job_id).status_code, 404)

    def test_deleting_a_series_with_episodes_removes_their_files(self) -> None:
        created = self.client.post("/api/v1/comic/series", data={
            "title": "Tiên Nghịch", "text": STORY, "llm_provider": "heuristic", "image_provider": "placeholder",
            "aspect_ratio": "16:9", "target_scene_duration": "4"})
        series_id, episode = created.json()["series_id"], created.json()["job_id"]
        self.assertEqual(self.run_job(episode)["status"], "completed")
        owner = self.service.get(episode)["owner_user_id"]
        work = main.tenant_storage_path(main.WORK_DIR, owner, episode)
        deleted = self.client.delete(f"/api/v1/comic/series/{series_id}?with_episodes=true")
        self.assertEqual(deleted.json()["deleted_episodes"], 1)
        self.assertFalse(work.exists())
        self.assertEqual(self.project(episode).status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/comic/series/{series_id}").status_code, 404)

    def test_series_character_reference_is_shared(self) -> None:
        from PIL import Image

        series_id = self.client.post("/api/v1/comic/series", data={
            "title": "Truyện", "llm_provider": "heuristic", "image_provider": "placeholder"}).json()["series_id"]
        main.COMIC_SERIES.update("user-a", series_id, lambda s: s.set_characters([
            ComicCharacter(id="minh", name="Minh", appearance="young man")]))
        png = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(png, format="PNG")
        uploaded = self.client.post(f"/api/v1/comic/series/{series_id}/characters/minh/reference",
                                    files={"file": ("minh.png", png.getvalue(), "image/png")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        minh = uploaded.json()["characters"][0]
        self.assertTrue(minh["approved_reference"].startswith("minh-upload-"))
        self.assertEqual(self.client.get(minh["reference_url"]).headers["content-type"], "image/png")
        patched = self.client.patch(f"/api/v1/comic/series/{series_id}/characters/minh", json={"outfit": "white robe"})
        self.assertEqual(patched.json()["characters"][0]["outfit"], "white robe")
        cleared = self.client.delete(f"/api/v1/comic/series/{series_id}/characters/minh/reference")
        self.assertIsNone(cleared.json()["characters"][0]["reference_url"])


if __name__ == "__main__":
    unittest.main()
