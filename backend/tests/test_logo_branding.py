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
from pipeline import logo_inpaint, logo_tracking
from pipeline.logo_overlay import build_filter_graph, logo_size, path_expression, plan_placements

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
            patch.object(main, "BRANDING_STORE", store),
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

    def test_standalone_logo_video_endpoints_are_gone(self):
        # Logos are stamped by translation/comic jobs (apply_logo); there is no upload-to-brand flow.
        self.assertIn(self.client.post("/api/v1/branding/videos").status_code, {404, 405})
        self.assertEqual(self.client.get("/api/v1/branding/videos").status_code, 404)

    def test_apply_logo_requires_an_account_logo(self):
        with self.assertRaises(main.HTTPException) as caught:
            main.require_account_logo("user-a")
        self.assertEqual(caught.exception.status_code, 400)
        self.upload_logo()
        main.require_account_logo("user-a")

    def test_display_name_update(self):
        with patch.object(main.BRANDING_ROUTE_DEPS, "update_display_name",
                          lambda user_id, name: {**self.users["a"], "display_name": name}):
            response = self.client.put("/api/v1/me", json={"display_name": "Kênh A"})
        self.assertEqual(response.json()["display_name"], "Kênh A")

    def test_template_upload_is_per_account(self):
        response = self.client.post("/api/v1/me/branding/template",
                                    files={"file": ("old.png", png_bytes((80, 30)), "image/png")})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["template"]["url"].startswith("/api/v1/me/branding/template"))
        self.assertIsNone(self.client.get("/api/v1/me/branding", headers={"x-test-user": "b"}).json()["template"])
        self.client.delete("/api/v1/me/branding/template")
        self.assertIsNone(self.client.get("/api/v1/me/branding").json()["template"])

    def finished_video(self, job_id: str) -> Path:
        self.service.create(job_id, {"status": "processing", "step": "rendering", "progress": 0.9},
                            owner_user_id="user-a")
        video = self.root / "outputs" / f"output_{job_id}.mp4"
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=640x360:r=25:d=3", "-f", "lavfi", "-i", "sine=f=440:d=3",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video)],
                       check=True)
        return video

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg is required")
    def test_finished_video_is_stamped_in_place_and_keeps_audio(self):
        self.upload_logo()
        video = self.finished_video("job1")
        before = video.read_bytes()
        main.stamp_account_logo("job1", "user-a", video, detect=False)
        job = self.service.get("job1")
        self.assertNotIn("error", job["branding"])
        self.assertEqual(job["branding"]["override"], "top-right")
        self.assertEqual(len(job["branding"]["placements"]), 1)
        self.assertNotEqual(video.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in video.parent.iterdir()), [video.name])
        streams = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type",
                                  "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(streams), ["audio", "video"])

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg is required")
    def test_stamping_failure_keeps_the_unbranded_video(self):
        video = self.finished_video("job2")  # no logo uploaded
        before = video.read_bytes()
        main.stamp_account_logo("job2", "user-a", video, detect=True)
        self.assertIn("Không chèn được logo", self.service.get("job2")["branding"]["error"])
        self.assertEqual(video.read_bytes(), before)


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



def evaluate_expression(expression: str, t: float) -> float:
    """Evaluate an FFmpeg position expression the way the overlay filter would."""
    return float(eval(expression.replace("\\,", ","), {"lt": lambda a, b: float(a < b),
                                                        "gte": lambda a, b: float(a >= b), "t": t}))


class TrackedPathTests(unittest.TestCase):
    def test_path_expression_interpolates_and_holds_at_both_ends(self):
        keyframes = [(1.0, 100, 50), (3.0, 300, 50), (4.0, 300, 150)]
        x, y = path_expression(keyframes, 0), path_expression(keyframes, 1)
        for t, expected in ((0.0, (100, 50)), (2.0, (200, 50)), (3.5, (300, 100)), (9.0, (300, 150))):
            self.assertAlmostEqual(evaluate_expression(x, t), expected[0], delta=0.5)
            self.assertAlmostEqual(evaluate_expression(y, t), expected[1], delta=0.5)

    def test_simplify_path_keeps_only_the_turns(self):
        points = [(t * 0.25, 0.1 + t * 0.01, 0.2) for t in range(20)] + [(5 + t * 0.25, 0.3, 0.2 + t * 0.01) for t in range(20)]
        kept = logo_tracking.simplify_path(points, 0.004)
        self.assertLessEqual(len(kept), 4)
        self.assertEqual((kept[0], kept[-1]), (points[0], points[-1]))

    def test_segments_split_on_jumps_close_small_gaps_and_drop_blips(self):
        box = lambda x, y: Box(x, y, 0.1, 0.05)
        samples = [(i * 0.25, 0.9, box(0.05, 0.05)) for i in range(8)]           # 0-1.75 s top-left
        samples += [(2.0, 0.9, None)]                                             # one missed sample
        samples += [(2.25 + i * 0.25, 0.9, box(0.05, 0.05)) for i in range(4)]   # back, same place
        samples += [(3.25 + i * 0.25, 0.9, box(0.85, 0.9)) for i in range(8)]    # jump to bottom-right
        samples += [(5.25, 0.9, box(0.4, 0.4))]                                   # single blip
        segments = logo_tracking.build_segments(samples, 6.0, 0.25)
        self.assertEqual(len([s for s in segments if s.box]), 2)
        first, second = [s for s in segments if s.box]
        self.assertEqual(first.start, 0.0)
        self.assertAlmostEqual(first.end, second.start)
        self.assertGreater(second.box.x, 0.8)
        self.assertIsNone(first.keyframes)

    def test_tracked_segment_becomes_a_moving_placement(self):
        segment = Segment(0, 10, Box(0.1, 0.1, 0.1, 0.05), 0.9, track=0,
                          keyframes=[(0.0, 0.15, 0.125), (10.0, 0.85, 0.125)])
        placements = plan_placements([segment], duration=10, video_w=1000, video_h=500, logo_w=100, logo_h=100,
                                     size_percent=10, margin_percent=0, default_position="top-right",
                                     cover_original=True, cover_method="inpaint")
        placement = placements[0]
        self.assertEqual(placement.source, "tracked")
        self.assertEqual([(t, x) for t, x, _ in placement.keyframes], [(0.0, 100), (10.0, 800)])
        self.assertEqual(placement.cover_keyframes, tuple(segment.keyframes))
        self.assertIsNone(placement.cover)  # inpainting, not delogo
        self.assertIn("x='", build_filter_graph(placements, 1.0))

    def test_blur_cannot_follow_a_moving_logo(self):
        segment = Segment(0, 10, Box(0.1, 0.1, 0.1, 0.05), 0.9, keyframes=[(0.0, 0.15, 0.1), (10.0, 0.8, 0.1)])
        placement = plan_placements([segment], duration=10, video_w=1000, video_h=500, logo_w=100, logo_h=100,
                                    size_percent=10, margin_percent=0, default_position="top-right",
                                    cover_original=True, cover_method="blur")[0]
        self.assertIsNone(placement.cover)
        self.assertIsNone(placement.cover_box)


@unittest.skipUnless(logo_tracking.available(), "OpenCV is required")
class TemplateTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.template_path = Path(self.temp.name) / "template.png"
        logo = Image.new("RGB", (120, 40), "white")
        logo.paste(Image.new("RGB", (100, 30), "red"), (10, 5))
        logo.paste(Image.new("RGB", (30, 14), "black"), (20, 13))
        logo.paste(Image.new("RGB", (40, 14), "blue"), (64, 13))
        logo.save(self.template_path)
        self.logo = np.asarray(Image.open(self.template_path).convert("L").resize((64, 21)))

    def fake_frames(self, positions):
        rng = np.random.default_rng(3)

        def frames(ffmpeg, video_path, fps, width, height):
            count = max(1, int(round(8 * fps)))
            for index in range(count):
                t = index / fps
                frame = (rng.random((height, width)) * 60 + 90).astype(np.uint8)
                position = positions(t)
                if position is not None:
                    x, y = int(position[0] * width), int(position[1] * height)
                    frame[y:y + 21, x:x + 64] = self.logo
                yield t, frame
        return frames

    def track(self, positions):
        return logo_tracking.track_template("ffmpeg", Path("unused.mp4"), self.template_path, width=640,
                                            height=360, duration=8.0, frames=self.fake_frames(positions))

    def test_moving_logo_is_followed_with_keyframes(self):
        detection = self.track(lambda t: (0.05 + t * 0.09, 0.1 + t * 0.03))
        self.assertEqual(detection.status, "found")
        segment = next(s for s in detection.segments if s.box)
        self.assertIsNotNone(segment.keyframes)
        times = [k[0] for k in segment.keyframes]
        cx = np.interp(4.0, times, [k[1] for k in segment.keyframes])
        self.assertAlmostEqual(cx, 0.05 + 4.0 * 0.09 + 32 / 640, delta=0.01)
        self.assertIn(0, detection.stencils)

    def test_absent_logo_is_not_found(self):
        detection = self.track(lambda t: None)
        self.assertNotEqual(detection.status, "found")
        self.assertTrue(all(segment.box is None for segment in detection.segments))


@unittest.skipUnless(logo_inpaint.available(), "OpenCV is required")
class InpaintTests(unittest.TestCase):
    def test_solid_mask_fills_the_inside_of_outlined_letters(self):
        stencil = np.zeros((20, 40), dtype=bool)
        stencil[2, 2:38] = stencil[17, 2:38] = True
        stencil[2:18, 2] = stencil[2:18, 37] = True
        mask = logo_inpaint.solid_mask(stencil, 40, 20, 0)
        self.assertEqual(mask[10, 20], 255)
        self.assertEqual(mask[0, 0], 0)

    def test_repair_replaces_logo_pixels_with_the_surrounding_background(self):
        frame = np.full((200, 300, 3), 90, dtype=np.uint8)
        frame[40:60, 50:110] = 250
        placement = plan_placements(
            [Segment(0, 5, Box(50 / 300, 40 / 200, 60 / 300, 20 / 200), 0.9, track=0)], duration=5,
            video_w=300, video_h=200, logo_w=10, logo_h=10, size_percent=3, margin_percent=0,
            default_position="top-right", cover_original=True, cover_method="inpaint")[0]
        stencil = np.ones((10, 30), dtype=bool)
        repairer = logo_inpaint.FrameRepairer([placement], {0: stencil}, 300, 200)
        self.assertTrue(repairer.repair(frame, 1.0))
        self.assertLess(abs(float(frame[40:60, 50:110].mean()) - 90), 6)


@unittest.skipUnless(HAS_FFMPEG and logo_inpaint.available(), "ffmpeg and OpenCV are required")
class InpaintRenderTests(unittest.TestCase):
    def test_static_logo_is_inpainted_and_replaced_end_to_end(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        # Like real logos it has contrast of its own, not only against the scene behind it.
        old_logo = root / "old.png"
        image = Image.new("RGB", (120, 36), (255, 0, 0))
        image.paste(Image.new("RGB", (104, 20), (255, 255, 255)), (8, 8))
        image.paste(Image.new("RGB", (30, 10), (0, 0, 0)), (20, 13))
        image.save(old_logo)
        source = root / "src.mp4"
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=640x360:r=25:d=8", "-f", "lavfi", "-i", "sine=f=440:d=8", "-i", str(old_logo),
                        "-filter_complex", "[0:v]rotate=t*0.3:fillcolor=gray[bg];[bg][2:v]overlay=24:20[v]",
                        "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                        str(source)], check=True)
        new_logo = root / "logo.png"
        new_logo.write_bytes(png_bytes((40, 40), color=(0, 0, 255, 255)))
        updates = {}
        runner = BrandingJobRunner(
            update_job=lambda job_id, **fields: updates.update(fields), ensure_active=lambda job_id: None,
            output_dir=lambda owner, job_id: root, artifact_name=lambda path: path.name, get_job=lambda job_id: {},
            ffmpeg=lambda: shutil.which("ffmpeg"), ffprobe=lambda: shutil.which("ffprobe"), utc_now=lambda: "now",
        )
        runner.run("job", {"video_path": str(source), "logo_path": str(new_logo), "logo_width": 40,
                           "logo_height": 40, "config": {"cover_original": True, "logo_size": 4}})
        self.assertEqual(updates["branding"]["cover_method"], "inpaint")
        self.assertTrue(updates["branding"]["placements"][0]["cover_box"])
        frame = subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-ss", "4", "-i", str(root / updates["output_video"]),
                                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                               capture_output=True, check=True).stdout
        pixels = np.frombuffer(frame, dtype=np.uint8).reshape(360, 640, 3).astype(int)
        # The ends of the old red bar lie outside the small new logo: they must no longer be red.
        corner = pixels[24:52, 26:40]
        self.assertLess(float((corner[:, :, 0] - corner[:, :, 1:].mean(axis=2)).mean()), 60)


if __name__ == "__main__":
    unittest.main()
