import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

import main
from application.branding_service import BrandingJobRunner
from application.job_service import JobService
from domain.branding import BrandingConfig
from infrastructure.branding_store import BrandingError, BrandingStore
from infrastructure.job_store import SQLiteJobStore
from pipeline.logo_detection import Box, Candidate, Segment, Window, decide, detect_candidates
from pipeline.logo_overlay import build_filter_graph, logo_size, plan_placements

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def png_bytes(size=(200, 100), color=(255, 0, 0, 255), padding=0) -> bytes:
    image = Image.new("RGBA", (size[0] + 2 * padding, size[1] + 2 * padding), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", size, color), (padding, padding))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def moving_frames(count=9, width=320, height=180, seed=1) -> np.ndarray:
    """Random textured scene that changes every frame."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(count, height, width), dtype=np.uint8)


def stamp_logo(frames: np.ndarray, x: int, y: int, w: int = 48, h: int = 20) -> np.ndarray:
    frames = frames.copy()
    frames[:, y:y + h, x:x + w] = 255
    frames[:, y + 4:y + h - 4, x + 4:x + w - 4] = 0
    frames[:, y + 8:y + h - 8, x + 12:x + w - 12] = 255
    return frames


class LogoDetectionTests(unittest.TestCase):
    def test_static_logo_on_moving_scene_is_found_in_its_corner(self):
        valid, candidates = detect_candidates(stamp_logo(moving_frames(), 260, 10))
        self.assertTrue(valid)
        self.assertTrue(candidates)
        box = candidates[0].box
        self.assertAlmostEqual(box.x, 260 / 320, delta=0.03)
        self.assertAlmostEqual(box.y, 10 / 180, delta=0.03)
        self.assertGreaterEqual(candidates[0].confidence, 0.55)

    def test_moving_scene_without_logo_has_no_candidates(self):
        valid, candidates = detect_candidates(moving_frames())
        self.assertTrue(valid)
        self.assertEqual(candidates, [])

    def test_static_scene_is_uncertain_instead_of_guessing(self):
        frames = np.repeat(moving_frames(1), 9, axis=0)
        self.assertEqual(detect_candidates(frames), (False, []))

    def test_logo_in_the_middle_of_the_frame_is_ignored(self):
        _, candidates = detect_candidates(stamp_logo(moving_frames(), 136, 80))
        self.assertEqual(candidates, [])

    def test_decide_segments_follow_a_logo_that_changes_corner(self):
        top_left, bottom_right = Box(0.02, 0.03, 0.15, 0.08), Box(0.82, 0.88, 0.15, 0.08)
        windows = [
            Window(0, 6, True, [Candidate(top_left, 0.9)]),
            Window(6, 12, True, [Candidate(top_left, 0.9)]),
            Window(12, 18, True, [Candidate(bottom_right, 0.9)]),
            Window(18, 24, True, []),
        ]
        detection = decide(windows, 24.0)
        self.assertEqual(detection.status, "found")
        self.assertEqual([(s.start, s.end, s.box) for s in detection.segments],
                         [(0.0, 12, top_left), (12, 18, bottom_right), (18, 24.0, None)])

    def test_uncertain_windows_follow_their_neighbours(self):
        box = Box(0.8, 0.03, 0.15, 0.08)
        windows = [Window(0, 6, False), Window(6, 12, True, [Candidate(box, 0.9)]), Window(12, 18, False)]
        detection = decide(windows, 18.0)
        self.assertEqual([(s.start, s.end, s.box) for s in detection.segments], [(0.0, 18.0, box)])

    def test_several_logos_pick_the_one_nearest_the_default_corner(self):
        left, right = Box(0.02, 0.03, 0.15, 0.08), Box(0.8, 0.03, 0.15, 0.08)
        windows = [Window(0, 6, True, [Candidate(left, 0.95), Candidate(right, 0.8)])]
        self.assertEqual(decide(windows, 6.0, default_position="top-right").segments[0].box, right)
        self.assertEqual(decide(windows, 6.0, strategy="most-confident").segments[0].box, left)

    def test_weak_candidates_are_reported_as_uncertain(self):
        windows = [Window(0, 6, True, [Candidate(Box(0.8, 0.03, 0.15, 0.08), 0.4)])]
        detection = decide(windows, 6.0, min_confidence=0.55)
        self.assertEqual(detection.status, "uncertain")
        self.assertIsNone(detection.segments[0].box)

    def test_no_readable_window_is_uncertain(self):
        self.assertEqual(decide([Window(0, 6, False)], 6.0).status, "uncertain")


class LogoOverlayTests(unittest.TestCase):
    def test_logo_size_tracks_video_width_and_keeps_aspect(self):
        for video_w, video_h in ((1280, 720), (1920, 1080), (3840, 2160), (1080, 1920)):
            width, height = logo_size(video_w, video_h, 400, 200, 12)
            self.assertAlmostEqual(width, video_w * 0.12, delta=2)
            self.assertAlmostEqual(width / height, 2.0, delta=0.05)
            self.assertEqual((width % 2, height % 2), (0, 0))

    def test_tall_logo_is_capped_by_frame_height_without_distortion(self):
        width, height = logo_size(1920, 1080, 100, 1000, 40)
        self.assertLessEqual(height, 1080 * 0.3 + 2)
        self.assertAlmostEqual(height / width, 10, delta=0.3)

    def test_no_detection_uses_the_default_top_right_corner(self):
        placements = plan_placements(None, duration=10, video_w=1920, video_h=1080, logo_w=200, logo_h=100,
                                     size_percent=10, margin_percent=2, default_position="top-right")
        self.assertEqual(len(placements), 1)
        placement = placements[0]
        self.assertEqual((placement.source, placement.y), ("default", round(1080 * 0.02)))
        self.assertEqual(placement.x + placement.width, 1920 - round(1080 * 0.02))

    def test_detected_segments_and_gaps_get_their_own_placements(self):
        segments = [Segment(0, 5, Box(0.02, 0.03, 0.1, 0.05), 0.9), Segment(5, 10, None, 0)]
        placements = plan_placements(segments, duration=10, video_w=1280, video_h=720, logo_w=100, logo_h=100,
                                     size_percent=10, margin_percent=0, default_position="bottom-left",
                                     cover_original=True)
        self.assertEqual([p.source for p in placements], ["detected", "default"])
        self.assertIsNotNone(placements[0].cover)
        self.assertIsNone(placements[1].cover)
        graph = build_filter_graph(placements, 0.8)
        self.assertIn("delogo=", graph)
        self.assertIn("split=2", graph)
        self.assertIn("colorchannelmixer=aa=0.800", graph)
        self.assertIn("lt(t\\,5.000)", graph)

    def test_manual_override_ignores_detection(self):
        segments = [Segment(0, 10, Box(0.02, 0.03, 0.1, 0.05), 0.9)]
        placements = plan_placements(segments, duration=10, video_w=1280, video_h=720, logo_w=100, logo_h=100,
                                     size_percent=10, margin_percent=0, default_position="top-right",
                                     override="bottom-left")
        self.assertEqual([(p.source, p.x, p.y + p.height) for p in placements], [("manual", 0, 720)])


class BrandingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = BrandingStore(root / "db.sqlite3", lambda user_id: root / "users" / user_id)

    def test_defaults_then_saved_settings_are_per_user(self):
        self.assertEqual(self.store.get("a")["config"], BrandingConfig())
        self.store.save_config("a", BrandingConfig(default_position="bottom-left", logo_size=20))
        self.assertEqual(self.store.get("a")["config"].default_position, "bottom-left")
        self.assertEqual(self.store.get("b")["config"].default_position, "top-right")

    def test_logo_is_trimmed_reencoded_and_replaced(self):
        first = self.store.save_logo("a", png_bytes((120, 60), padding=20))
        self.assertEqual((first["logo"]["width"], first["logo"]["height"]), (120, 60))
        old_path = self.store.logo_path("a")
        self.store.save_logo("a", png_bytes((50, 50), color=(0, 0, 255, 255)))
        self.assertFalse(old_path.exists())
        self.assertIsNone(self.store.logo_path("b"))
        self.store.delete_logo("a")
        self.assertIsNone(self.store.get("a")["logo"])

    def test_invalid_logos_are_rejected(self):
        with self.assertRaises(BrandingError):
            self.store.save_logo("a", b"not an image")
        with self.assertRaises(BrandingError):
            self.store.save_logo("a", png_bytes((50, 50), color=(0, 0, 0, 0)))


def account(user_id: str, role: str = "editor") -> dict:
    return {"user_id": user_id, "email": f"{user_id}@test.local", "display_name": user_id, "role": role,
            "plan": "free", "approval_status": "approved"}


class BrandingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name in ("outputs", "uploads", "work"):
            (root / name).mkdir()
        self.root = root
        self.service = JobService(SQLiteJobStore(root / "jobs.sqlite3"))
        self.users = {"a": account("user-a"), "b": account("user-b")}
        store = BrandingStore(root / "branding.sqlite3", lambda user_id: root / "work" / "users" / user_id)
        deps = main.BRANDING_ROUTE_DEPS
        patches = [
            patch.object(main, "JOB_SERVICE", self.service),
            patch.object(main, "OUTPUT_DIR", root / "outputs"),
            patch.object(main, "UPLOAD_DIR", root / "uploads"),
            patch.object(main, "WORK_DIR", root / "work"),
            patch.object(main, "TENANT_GOVERNANCE", Mock()),
            patch.object(main, "reserve_job_quota", Mock()),
            patch.object(main, "notify_scheduler", Mock()),
            patch.object(deps, "store", store),
            patch.object(deps, "validate_video", Mock()),
            patch.object(main, "current_account",
                         lambda request: self.users[request.headers.get("x-test-user", "a")]),
        ]
        if HAS_FFMPEG:
            patches += [patch.object(main, "FFMPEG", shutil.which("ffmpeg")),
                        patch.object(main, "FFPROBE", shutil.which("ffprobe"))]
        for active in patches:
            active.start()
            self.addCleanup(active.stop)
        self.client = TestClient(main.app)

    def upload_logo(self, user="a"):
        return self.client.post("/api/v1/me/branding/logo", headers={"x-test-user": user},
                                files={"file": ("logo.png", png_bytes(), "image/png")})

    def queue(self, user="a", rights="true", content=b"fake video"):
        return self.client.post("/api/v1/branding/videos", headers={"x-test-user": user},
                                data={"rights_confirmed": rights},
                                files={"file": ("clip.mp4", content, "video/mp4")})

    def test_settings_and_logo_are_isolated_per_account(self):
        response = self.client.put("/api/v1/me/branding", json={"default_position": "bottom-left", "logo_size": 15})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.upload_logo().status_code, 200)
        mine = self.client.get("/api/v1/me/branding").json()
        theirs = self.client.get("/api/v1/me/branding", headers={"x-test-user": "b"}).json()
        self.assertEqual(mine["config"]["default_position"], "bottom-left")
        self.assertTrue(mine["logo"]["url"].startswith("/api/v1/me/branding/logo"))
        self.assertEqual(theirs["config"]["default_position"], "top-right")
        self.assertIsNone(theirs["logo"])
        self.assertEqual(self.client.get("/api/v1/me/branding/logo", headers={"x-test-user": "b"}).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/me/branding/logo").headers["content-type"], "image/png")

    def test_invalid_settings_and_logo_are_rejected(self):
        self.assertEqual(self.client.put("/api/v1/me/branding", json={"logo_size": 90}).status_code, 422)
        self.assertEqual(self.client.put("/api/v1/me/branding", json={"unknown": 1}).status_code, 422)
        response = self.client.post("/api/v1/me/branding/logo", files={"file": ("x.png", b"nope", "image/png")})
        self.assertEqual(response.status_code, 400)

    def test_queueing_requires_logo_and_rights_confirmation(self):
        self.assertEqual(self.queue().status_code, 400)  # no logo yet
        self.upload_logo()
        self.assertEqual(self.queue(rights="false").status_code, 400)
        response = self.queue()
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        job = self.service.get(job_id)
        self.assertEqual(job["job_action"], "logo_branding")
        self.assertTrue(Path(job["resume_request"]["logo_path"]).is_file())
        self.assertNotIn("resume_request", self.client.get(f"/api/v1/branding/videos/{job_id}").json())
        # Another account can neither see the job nor find it in its history.
        self.assertEqual(self.client.get(f"/api/v1/branding/videos/{job_id}", headers={"x-test-user": "b"}).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/branding/videos", headers={"x-test-user": "b"}).json()["total"], 0)
        self.assertEqual(self.client.get("/api/v1/branding/videos").json()["total"], 1)

    def test_display_name_update(self):
        with patch.object(main.BRANDING_ROUTE_DEPS, "update_display_name",
                          lambda user_id, name: {**self.users["a"], "display_name": name}):
            response = self.client.put("/api/v1/me", json={"display_name": "Kênh A"})
        self.assertEqual(response.json()["display_name"], "Kênh A")

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg is required")
    def test_queued_job_renders_with_logo_and_keeps_audio(self):
        source = self.root / "src.mp4"
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=640x360:r=25:d=3", "-f", "lavfi", "-i", "sine=f=440:d=3",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(source)],
                       check=True)
        self.upload_logo()
        job_id = self.queue(content=source.read_bytes()).json()["job_id"]
        request = dict(self.service.get(job_id)["resume_request"])
        main.run_branding_job(job_id, request)
        job = self.client.get(f"/api/v1/branding/videos/{job_id}").json()
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertEqual(len(job["branding"]["placements"]), 1)
        download = self.client.get(f"/api/v1/branding/videos/{job_id}/download")
        self.assertEqual(download.status_code, 200)
        output = self.root / "out.mp4"
        output.write_bytes(download.content)
        streams = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type",
                                  "-of", "csv=p=0", str(output)], capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(streams), ["audio", "video"])
        # Re-run with a manual corner after review.
        response = self.client.post(f"/api/v1/branding/videos/{job_id}/reprocess", json={"override": "bottom-left"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.service.get(job_id)["resume_request"]["override"], "bottom-left")


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg is required")
class BrandingRunnerCancellationTests(unittest.TestCase):
    def test_cancelled_render_leaves_no_partial_file(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        source = root / "src.mp4"
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=640x360:r=25:d=4", "-c:v", "libx264", "-preset", "ultrafast", str(source)],
                       check=True)
        logo = root / "logo.png"
        logo.write_bytes(png_bytes())

        class Cancelled(RuntimeError):
            pass

        calls = {"n": 0}

        def ensure_active(job_id):
            calls["n"] += 1
            if calls["n"] > 1:
                raise Cancelled()

        runner = BrandingJobRunner(
            update_job=Mock(), ensure_active=ensure_active, output_dir=lambda owner, job_id: root,
            artifact_name=lambda path: path.name, get_job=lambda job_id: {}, ffmpeg=lambda: shutil.which("ffmpeg"),
            ffprobe=lambda: shutil.which("ffprobe"), utc_now=lambda: "now",
        )
        with self.assertRaises(Cancelled):
            runner.run("job", {"video_path": str(source), "logo_path": str(logo), "logo_width": 200,
                               "logo_height": 100, "config": {"auto_detect": False}})
        self.assertEqual(sorted(path.name for path in root.iterdir()), ["logo.png", "src.mp4"])


if __name__ == "__main__":
    unittest.main()
