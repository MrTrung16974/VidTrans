import io
import shutil
import unittest
from unittest.mock import patch

from tests import test_comic_api as comic_api

STORY = ("Đêm hôm đó, Minh bước vào khu rừng. Ánh trăng lạnh lẽo xuyên qua tán lá.\n\n"
         "\"Có ai ở đây không?\" Minh gọi lớn. Không ai trả lời.")


def png_bytes(color=(0, 120, 0)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is required")
class AnimationApiTests(comic_api.ComicApiTests):
    """Reuses the comic API fixture (temp storage, fake voice, per-user auth)."""

    def create(self, **form):
        data = {"title": "Rừng đêm", "text": STORY, "llm_provider": "heuristic", "image_provider": "placeholder",
                "aspect_ratio": "16:9", "video_mode": "animated", "animation_provider": "camera_2_5d",
                "quality": "draft", "fps": "24", **form}
        return self.client.post("/api/v1/comic/projects", data=data)

    # The comic-mode scenarios are covered by ComicApiTests itself.
    test_full_flow_create_render_edit_and_rerender = None
    test_validation_errors_are_rejected = None
    test_docx_upload_and_images_only_preview = None
    test_scene_edits_are_validated_and_owner_scoped = None
    test_uploaded_scene_image_and_generic_retry_keep_the_project = None
    test_options_list_styles_and_providers = None

    def test_animated_flow_render_edit_regenerate_and_preview(self) -> None:
        created = self.create()
        self.assertEqual(created.status_code, 202, created.text)
        job_id = created.json()["job_id"]
        job = self.run_job(job_id)
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertEqual(job["comic"]["video_mode"], "animated")
        self.assertGreaterEqual(job["comic"]["shots"], 2)

        payload = self.project(job_id).json()
        self.assertEqual(payload["storyboard"]["mode"], "animated")
        self.assertIn("1.1", payload["media"]["keyframes"])
        animation = self.client.get(payload["media"]["animations"]["1.1"])
        self.assertEqual((animation.status_code, animation.headers["content-type"]), (200, "video/mp4"))
        self.assertEqual(payload["shots"]["1.1"]["provider"], "camera_2_5d")
        self.assertNotIn("cues", payload["shots"]["1.1"])

        base = f"/api/v1/comic/projects/{job_id}/scenes/1/shots/1"
        patched = self.client.patch(base, json={"camera": {"movement": "pan_left"}, "motion_prompt": "wind blows"})
        self.assertEqual(patched.status_code, 200, patched.text)
        shot = patched.json()["storyboard"]["scenes"][0]["shots"][0]
        self.assertEqual((shot["camera"]["movement"], shot["camera"]["shot_type"]), ("pan_left", "wide"))
        self.assertEqual(shot["version"], 2)
        regenerated = self.client.post(f"{base}/regenerate").json()["storyboard"]["scenes"][0]["shots"][0]
        self.assertEqual(regenerated["animation"]["take"], 2)

        preview = self.client.post(f"/api/v1/comic/projects/{job_id}/render", json={"shot": "1.1"},
                                   headers={"Idempotency-Key": "preview-1"})
        self.assertEqual(preview.status_code, 202, preview.text)
        duplicate = self.client.post(f"/api/v1/comic/projects/{job_id}/render", json={"shot": "1.1"},
                                     headers={"Idempotency-Key": "preview-1"})
        self.assertEqual((duplicate.status_code, duplicate.json().get("duplicate")), (200, True))
        job = self.run_job(job_id)
        self.assertEqual((job["status"], job["step"]), ("completed", "shot_preview_ready"))
        preview_url = self.project(job_id).json()["media"]["previews"]["1.1"]
        self.assertEqual(self.client.get(preview_url).headers["content-type"], "video/mp4")
        self.assertEqual(self.client.post(f"/api/v1/comic/projects/{job_id}/render",
                                          json={"shot": "9.9"}).status_code, 404)

    def test_shot_crud_keyframe_upload_and_references(self) -> None:
        job_id = self.create(auto_render="false").json()["job_id"]
        job = self.run_job(job_id)
        self.assertEqual(job["step"], "storyboard_ready")
        base = f"/api/v1/comic/projects/{job_id}"

        inserted = self.client.post(f"{base}/scenes/1/shots", json={"after": 0, "keyframe_prompt": "Title card"})
        shots = inserted.json()["storyboard"]["scenes"][0]["shots"]
        self.assertEqual(shots[0]["keyframe_prompt"], "Title card")
        self.assertEqual([s["shot_index"] for s in shots], list(range(1, len(shots) + 1)))
        for _ in range(len(shots) - 1):
            self.assertEqual(self.client.delete(f"{base}/scenes/1/shots/1").status_code, 200)
        self.assertEqual(self.client.delete(f"{base}/scenes/1/shots/1").status_code, 409)
        self.assertEqual(self.client.patch(f"{base}/scenes/1/shots/7", json={"action": "x"}).status_code, 404)
        self.assertEqual(self.client.patch(f"{base}/scenes/1/shots/1", json={"duration_seconds": 30}).status_code, 422)

        upload = self.client.post(f"{base}/scenes/1/shots/1/keyframe", files={"file": ("k.png", png_bytes(), "image/png")})
        self.assertTrue(upload.json()["storyboard"]["scenes"][0]["shots"][0]["keyframe_override"].startswith("k001-01-"))
        self.assertIn("1.1", upload.json()["media"]["keyframe_uploads"])

        # References: the heuristic splitter finds no named cast, so add one through the storyboard file.
        from infrastructure.comic_project import ComicProject

        project = ComicProject(main_project_root(job_id, self.service))
        board = project.load_storyboard()
        from domain.comic import ComicCharacter

        board.characters.append(ComicCharacter(id="minh", name="Minh", appearance="boy"))
        project.save_storyboard(board)
        ref = self.client.post(f"{base}/characters/minh/references", files={"file": ("r.png", png_bytes(), "image/png")})
        approved = next(c for c in ref.json()["storyboard"]["characters"] if c["id"] == "minh")["approved_reference"]
        self.assertTrue(approved.startswith("minh-upload-"))
        variants = self.client.get(f"{base}/characters/minh/references").json()["variants"]
        self.assertEqual([(v["name"], v["approved"]) for v in variants], [(approved, True)])
        self.assertEqual(self.client.post(f"{base}/characters/minh/references/lan-x.png/approve").status_code, 404)
        unpinned = self.client.delete(f"{base}/characters/minh/references/approval").json()
        self.assertIsNone(next(c for c in unpinned["storyboard"]["characters"] if c["id"] == "minh")["approved_reference"])

        self.assertEqual(self.client.get(f"{base}/characters/minh/references", headers={"x-test-user": "b"}).status_code,
                         404)

    def test_unconfigured_ai_provider_is_rejected(self) -> None:
        with patch.dict("os.environ", {"GEMINI_API_KEY": "", "VIDTRANS_ANIMATION_WORKER_URL": ""}):
            self.assertEqual(self.create(animation_provider="veo").status_code, 400)
            self.assertEqual(self.create(animation_provider="gpu_worker").status_code, 400)
        self.assertEqual(self.create(fps="25").status_code, 400)
        options = self.client.get("/api/v1/comic/options").json()
        self.assertIn("tracking", options["shot_movements"])
        self.assertEqual(options["animation_providers"][0]["key"], "camera_2_5d")


def main_project_root(job_id: str, service):
    import main

    owner = str(service.get(job_id).get("owner_user_id"))
    return main.comic_project_root(owner, job_id)


if __name__ == "__main__":
    unittest.main()
