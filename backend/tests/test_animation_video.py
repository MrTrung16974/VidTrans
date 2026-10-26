import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from domain.comic import COMIC_STYLES, ComicCharacter, ComicScene, ComicSettings, Shot, Storyboard, style_negative_prompt
from infrastructure.comic_project import ComicProject
from infrastructure.image_providers import PlaceholderImageProvider
from infrastructure.video_providers import (
    Camera25DProvider,
    GPUWorkerProvider,
    VeoProvider,
    VideoConfigurationError,
    VideoGenerationError,
    VideoResult,
    camera_expressions,
)
from pipeline.animation_qc import check_clip
from pipeline.animation_storyboard import heuristic_animated_scenes
from pipeline.animation_video import AnimatedVideoProducer, keyframe_prompt, motion_prompt
from pipeline.comic_storyboard import build_storyboard
from pipeline.comic_video import ComicRuntime

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
FONT = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSansCJKsc-Regular.otf"


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def duration(path):
    out = run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    return float(out.stdout)


def make_clip(path: Path, seconds: float, *, moving: bool = True, size=(320, 180)) -> None:
    source = f"testsrc2=size={size[0]}x{size[1]}:rate=24" if moving else f"color=c=gray:size={size[0]}x{size[1]}:rate=24"
    run([FFMPEG, "-y", "-f", "lavfi", "-i", source, "-t", str(seconds), "-pix_fmt", "yuv420p", str(path)])


class FakeAI:
    """Stands in for Veo / a GPU worker; ``script`` decides what each attempt returns."""

    key = "fake_ai"
    is_ai = True
    cache_key = "fake-ai-v1"

    def __init__(self, script=("moving",), seconds=4.0):
        self.script = list(script)
        self.seconds = seconds
        self.requests = []

    def generation_duration(self, requested):
        return min(requested, 6.0)

    def generate(self, request, output, check_active):
        self.requests.append(request)
        outcome = self.script.pop(0) if self.script else "moving"
        if outcome == "error":
            raise VideoGenerationError("GPU hết bộ nhớ")
        if outcome == "config":
            raise VideoConfigurationError("Sai API key")
        make_clip(output, self.seconds, moving=outcome == "moving")
        return VideoResult(model="fake-model", billed_seconds=self.seconds)


def animated_storyboard() -> Storyboard:
    return Storyboard(
        mode="animated",
        characters=[ComicCharacter(id="minh", name="Minh", gender="male", appearance="boy with short black hair")],
        scenes=[
            ComicScene(scene_index=1, narration="Minh vào rừng.", visual_prompt="dark forest at night", characters=["minh"],
                       shots=[
                           Shot(shot_index=1, duration_seconds=3, keyframe_prompt="Wide view of a moonlit forest",
                                camera={"shot_type": "establishing", "movement": "push_in"}),
                           Shot(shot_index=2, characters=["minh"], narration="Minh bước chậm vào rừng.",
                                keyframe_prompt="Minh on a forest path", motion_prompt="Minh walks slowly",
                                camera={"shot_type": "medium", "movement": "tracking"}),
                       ]),
            ComicScene(scene_index=2, narration="x", visual_prompt="forest path", shots=[
                Shot(shot_index=1, characters=["minh"], dialogue=[{"character": "minh", "text": "Có ai không?"}],
                     keyframe_prompt="Close-up of Minh, worried", camera={"shot_type": "close_up"}),
            ]),
        ],
    )


class StoryboardTests(unittest.TestCase):
    def test_heuristic_animated_storyboard_has_short_shots(self) -> None:
        text = ("Đêm hôm đó, Minh bước vào khu rừng. Ánh trăng lạnh lẽo xuyên qua tán lá. Gió rít từng hồi.\n\n"
                "\"Có ai ở đây không?\" Minh gọi lớn. Không ai trả lời.")
        board = build_storyboard(text, ComicSettings(video_mode="animated", llm_provider="heuristic"), None)
        self.assertEqual(board.mode, "animated")
        self.assertEqual(len(board.scenes), 2)
        shots = [shot for _scene, shot in board.iter_shots()]
        self.assertTrue(all(3 <= shot.duration_seconds <= 8 for shot in shots))
        self.assertTrue(all(shot.motion_prompt and shot.negative_prompt for shot in shots))

    def test_llm_drift_in_camera_enums_is_normalised(self) -> None:
        shot = Shot(shot_index=1, keyframe_prompt="abc",
                    camera={"shot_type": "Close-up", "movement": "dolly in", "angle": "Low angle"})
        self.assertEqual((shot.camera.shot_type, shot.camera.movement, shot.camera.angle), ("close_up", "push_in", "low"))
        self.assertEqual(Shot(shot_index=1, keyframe_prompt="abc", camera={"movement": "spin wildly"}).camera.movement,
                         "static")

    def test_animated_storyboard_requires_shots_and_known_cast(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one shot"):
            Storyboard(mode="animated", scenes=[ComicScene(scene_index=1, narration="a", visual_prompt="abc")])
        with self.assertRaisesRegex(ValueError, "unknown characters"):
            Storyboard(mode="animated", scenes=[ComicScene(scene_index=1, visual_prompt="abc", shots=[
                Shot(shot_index=1, keyframe_prompt="abc", characters=["ghost"])])])

    def test_llm_animated_draft_without_shots_is_repaired(self) -> None:
        from tests.test_comic_storyboard import STORY, FakeLLM

        no_shots = {"characters": [], "scenes": [{"visual_prompt": "forest", "narration": "x"}]}
        good = {"characters": [], "scenes": [{"visual_prompt": "forest", "shots": [
            {"characters": [], "action": "wind", "camera": {"shot_type": "wide"}, "narration": "Đêm hôm đó.",
             "dialogue": [], "keyframe_prompt": "moonlit forest", "motion_prompt": "leaves move"}]}]}
        llm = FakeLLM([no_shots, good])
        board = build_storyboard(STORY, ComicSettings(video_mode="animated", llm_provider="gemini"), llm)
        self.assertIn("have no shots", llm.prompts[1])
        self.assertEqual(board.scenes[0].shots[0].camera.shot_type, "wide")

    def test_prompts_carry_camera_cast_and_consistency_rules(self) -> None:
        board = animated_storyboard()
        scene, shot = board.scenes[0], board.scenes[0].shots[1]
        self.assertIn("boy with short black hair", keyframe_prompt(scene, shot, board, "16:9"))
        motion = motion_prompt(scene, shot, board)
        self.assertIn("tracking", motion)
        self.assertIn("identical to the first frame", motion)

    def test_keyframe_prompt_leads_with_action_and_drops_names(self) -> None:
        board = animated_storyboard()
        scene, shot = board.scenes[0], board.scenes[0].shots[1]
        name = board.character(shot.characters[0]).name
        shot.keyframe_prompt = f"{name} runs toward the gate"
        prompt = keyframe_prompt(scene, shot, board, "16:9")
        self.assertTrue(prompt.startswith(("the man runs", "the woman runs", "the person runs")), prompt)
        self.assertNotIn(name, prompt)
        style = COMIC_STYLES[board.style][1]
        self.assertGreater(prompt.index(style), prompt.index("runs toward the gate"))

    def test_character_prompts_state_gender_age_and_species(self) -> None:
        from pipeline.comic_video import cast_negative, character_look, reference_prompt

        man = ComicCharacter(id="doan_than", name="Doãn Thần", gender="male", age="20", appearance="short black hair")
        boy = ComicCharacter(id="b", name="Tí", gender="male", age="8")
        grandma = ComicCharacter(id="c", name="Bà Tư", gender="female", appearance="elderly, grey bun")
        cat = ComicCharacter(id="d", name="Mướp", gender="male", species="Black Cat", appearance="green eyes")
        self.assertTrue(character_look(man).startswith("a young man (male), short black hair"))
        self.assertTrue(character_look(ComicCharacter(id="f", name="F", gender="male")).startswith("a man (male)"))
        self.assertTrue(character_look(boy).startswith("a boy (male)"))
        self.assertTrue(character_look(grandma).startswith("an old woman (female)"))
        self.assertEqual(character_look(cat), "a male black cat, green eyes")
        self.assertNotIn("Doãn", reference_prompt(man, "manhua"))
        board = Storyboard(title="T", characters=[man, cat], scenes=[ComicScene(scene_index=1, narration="x", visual_prompt="a room")])
        self.assertIn("woman, girl, female", cast_negative(board, ["doan_than"], "text"))
        self.assertIn("human, person", cast_negative(board, ["d"], "text"))
        self.assertEqual(cast_negative(board, ["doan_than", "d"], "text").count("woman"), 1)
        self.assertEqual(ComicCharacter(id="e", name="E", species="Person").species, "human")
        from pipeline.comic_video import without_names
        self.assertEqual(without_names("Doan Than's face; doan_than nods", board),
                         "the young man's face; the young man nods")

    def test_xianxia_style_renders_live_action_film_frames(self) -> None:
        board = animated_storyboard()
        board.style = "xianxia"
        scene, shot = board.scenes[0], board.scenes[0].shots[1]
        keyframe = keyframe_prompt(scene, shot, board, "16:9")
        self.assertIn("Cinematic film keyframe", keyframe)
        self.assertNotIn("2D animation", keyframe)
        self.assertIn("live-action shot", motion_prompt(scene, shot, board))
        self.assertIn("anime", style_negative_prompt("xianxia", "text"))
        self.assertEqual(style_negative_prompt("manhua", "text"), "text")

    def test_camera_moves_scale_with_motion_strength(self) -> None:
        gentle = camera_expressions("push_in", 100, 0.0)[0]
        strong = camera_expressions("push_in", 100, 1.0)[0]
        self.assertNotEqual(gentle, strong)
        for move in ("static", "pan_left", "tilt_down", "tracking", "handheld", "pull_out"):
            self.assertTrue(all(camera_expressions(move, 50, 0.5)))

    def test_heuristic_scene_split_respects_shot_cap(self) -> None:
        scenes = heuristic_animated_scenes("Một câu. " * 60, ComicSettings(), 1, 14.0)
        self.assertTrue(all(len(scene.shots) <= 8 for scene in scenes))


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg is required")
class ProducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = ComicProject(self.root / "comic")
        self.voices = []

    def synthesize(self, text, path, gender, rate, pitch, language):
        self.voices.append(text)
        run([FFMPEG, "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={max(0.6, len(text) / 20)}",
             "-ac", "1", str(path)])

    def producer(self, ai, **settings):
        runtime = ComicRuntime(FFMPEG, run, duration, self.synthesize, FONT)
        options = {"aspect_ratio": "16:9", "quality": "draft", "fps": 24, "video_mode": "animated", **settings}
        return AnimatedVideoProducer(self.project, ComicSettings(**options), PlaceholderImageProvider(FONT), ai,
                                     Camera25DProvider(FFMPEG, run), runtime)

    def test_full_render_then_edit_one_shot(self) -> None:
        ai = FakeAI()
        board = self.project.save_storyboard(animated_storyboard(), bump=False)
        video, srt = self.root / "out.mp4", self.root / "out.srt"

        manifest = self.producer(ai).produce(board, video, srt)

        self.assertEqual(list(manifest["shots"]), ["1.1", "1.2", "2.1"])
        self.assertEqual(len(ai.requests), 3)
        self.assertAlmostEqual(duration(video), manifest["duration"], delta=0.15)
        self.assertEqual(manifest["metrics"]["fallback_shots"], 0)
        self.assertEqual(manifest["metrics"]["ai_seconds"], 12.0)
        # The silent establishing shot keeps its requested length; spoken shots fit their voice.
        self.assertAlmostEqual(manifest["shots"]["1.1"]["duration"], 3.0, places=1)
        self.assertIn("Có ai không?", srt.read_text(encoding="utf-8"))
        self.assertEqual(ai.requests[0].width, 1280)  # Draft quality renders 720p.

        board.scenes[0].shots[1].motion_prompt = "Minh runs"
        board = self.project.save_storyboard(board)
        ai.requests.clear()
        self.voices.clear()
        manifest = self.producer(ai).produce(board, video, srt)

        self.assertEqual(len(ai.requests), 1)  # Only the edited shot is animated again.
        self.assertIn("Minh runs", ai.requests[0].prompt)
        self.assertEqual(self.voices, [])  # Its voice and keyframe are reused.

    def test_failed_qc_retries_with_a_new_seed_then_falls_back_to_2_5d(self) -> None:
        ai = FakeAI(script=["frozen", "error"])
        board = animated_storyboard()
        board.scenes = board.scenes[:1]
        board.scenes[0].shots = board.scenes[0].shots[:1]
        manifest = self.producer(ai, max_animation_attempts=2).produce(board, self.root / "o.mp4", self.root / "o.srt")

        entry = manifest["shots"]["1.1"]
        self.assertTrue(entry["fallback"])
        self.assertEqual(entry["provider"], "camera_2_5d")
        self.assertEqual(entry["attempts"], 2)
        self.assertNotEqual(ai.requests[0].seed, ai.requests[1].seed)
        self.assertEqual(manifest["metrics"]["fallback_shots"], 1)
        meta = json.loads(next((self.project.root / "animations").glob("*.json")).read_text())
        self.assertIn("không chuyển động", json.dumps(meta, ensure_ascii=False).lower())

    def test_configuration_errors_stop_retrying_and_budget_caps_ai_seconds(self) -> None:
        ai = FakeAI(script=["config"])
        board = animated_storyboard()
        manifest = self.producer(ai, max_animation_attempts=3, animation_budget_seconds=5.5).produce(
            board, self.root / "o.mp4", self.root / "o.srt")
        # Shot 1.1 stops after the non-retryable error; 1.2 (5 s) fits the budget and bills 4 s;
        # 2.1 would exceed it.
        self.assertEqual(len(ai.requests), 2)
        self.assertEqual(manifest["metrics"]["fallback_shots"], 2)
        self.assertIn("ngân sách", manifest["shots"]["2.1"]["fallback_reason"])

    def test_regenerate_take_and_2_5d_shots_skip_the_ai(self) -> None:
        ai = FakeAI()
        board = animated_storyboard()
        board.scenes[0].shots[0].animation.mode = "camera_2_5d"
        producer = self.producer(ai)
        manifest = producer.produce(board, self.root / "o.mp4", self.root / "o.srt")
        self.assertEqual(manifest["shots"]["1.1"]["provider"], "camera_2_5d")
        self.assertFalse(manifest["shots"]["1.1"]["fallback"])
        self.assertEqual(len(ai.requests), 2)

        ai.requests.clear()
        board.scenes[1].shots[0].animation.take = 2
        manifest = self.producer(ai).preview_shot(board, "2.1")
        self.assertEqual(len(ai.requests), 1)
        self.assertIn("2.1", manifest["previews"])
        self.assertIn("1.2", manifest["shots"])  # The earlier full render is kept in the manifest.

    def test_keyframe_preview_and_approved_reference_are_used(self) -> None:
        from PIL import Image

        ref = self.project.dir("refs") / "minh-approved.png"
        Image.new("RGB", (64, 64), (0, 0, 255)).save(ref)
        board = animated_storyboard()
        board.characters[0].approved_reference = ref.name

        class RefImages(PlaceholderImageProvider):
            supports_references = True
            requests = []

            def generate(self, request, output_path):
                self.requests.append(request)
                super().generate(request, output_path)

        images = RefImages(FONT)
        producer = self.producer(FakeAI())
        producer.images = images
        manifest = producer.preview_images(board)
        self.assertEqual(manifest["references"], {"minh": "minh-approved.png"})
        self.assertEqual([r.caption for r in images.requests], ["Shot 1.1", "Shot 1.2", "Shot 2.1"])
        self.assertEqual(images.requests[1].references, (ref.resolve(),))

    def test_qc_flags_frozen_and_short_clips(self) -> None:
        good, frozen = self.root / "good.mp4", self.root / "frozen.mp4"
        make_clip(good, 3)
        make_clip(frozen, 3, moving=False)
        kwargs = {"ffmpeg": FFMPEG, "run_ffmpeg": run, "media_duration": duration}
        self.assertTrue(check_clip(good, 3, **kwargs).passed)
        self.assertFalse(check_clip(frozen, 3, **kwargs).passed)
        self.assertFalse(check_clip(good, 8, **kwargs).passed)
        self.assertFalse(check_clip(self.root / "missing.mp4", 3, **kwargs).passed)


class _WorkerHandler(BaseHTTPRequestHandler):
    clip: Path
    received: list = []
    paths: list = []
    polls = 0

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer secret":
            return self._json({"error": "unauthorized"}, 401)
        self.paths.append(self.path)
        self.received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        if self.path.endswith("/cancel"):
            return self._json({"job_id": "j1", "status": "cancelled"})
        self._json({"job_id": "j1", "status": "queued"})

    def do_GET(self):
        if self.path == "/v1/jobs/j1":
            type(self).polls += 1
            done = self.polls >= 2
            return self._json({"status": "completed" if done else "running", "gpu_seconds": 7.5, "model": "wan2.1-i2v"})
        if self.path == "/v1/jobs/j1/video":
            data = self.clip.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None
        self._json({"error": "not found"}, 404)


@unittest.skipUnless(FFMPEG, "ffmpeg is required")
class RemoteProviderTests(unittest.TestCase):
    def test_gpu_worker_contract_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / "worker.mp4"
            make_clip(clip, 2)
            keyframe = Path(temp) / "k.png"
            from PIL import Image

            Image.new("RGB", (32, 32)).save(keyframe)
            _WorkerHandler.clip, _WorkerHandler.received, _WorkerHandler.polls = clip, [], 0
            server = ThreadingHTTPServer(("127.0.0.1", 0), _WorkerHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
            env = {"VIDTRANS_ANIMATION_WORKER_URL": f"http://127.0.0.1:{server.server_port}",
                   "VIDTRANS_ANIMATION_WORKER_TOKEN": "secret", "VIDTRANS_ANIMATION_WORKER_MAX_SECONDS": "5"}
            with patch.dict("os.environ", env):
                provider = GPUWorkerProvider(sleeper=lambda s: None)
            from infrastructure.video_providers import VideoRequest

            output = Path(temp) / "out.mp4"
            result = provider.generate(VideoRequest(keyframe=keyframe, prompt="walk", duration_seconds=7, width=640,
                                                    height=360, fps=24, aspect_ratio="16:9", seed=5,
                                                    idempotency_key="k-1"), output, lambda: None)
            self.assertEqual(output.read_bytes(), clip.read_bytes())
            # Billed in seconds of video, not GPU time, so the render budget counts clips.
            self.assertEqual((result.model, result.billed_seconds), ("wan2.1-i2v", 5.0))
            self.assertEqual(result.metadata["gpu_seconds"], 7.5)
            sent = _WorkerHandler.received[0]
            self.assertEqual((sent["duration_seconds"], sent["seed"], sent["idempotency_key"]), (5.0, 5, "k-1"))
            self.assertTrue(sent["image_base64"])

    def test_cancelled_render_cancels_the_worker_clip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            keyframe = Path(temp) / "k.png"
            from PIL import Image

            Image.new("RGB", (32, 32)).save(keyframe)
            _WorkerHandler.received, _WorkerHandler.paths, _WorkerHandler.polls = [], [], 0
            server = ThreadingHTTPServer(("127.0.0.1", 0), _WorkerHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
            env = {"VIDTRANS_ANIMATION_WORKER_URL": f"http://127.0.0.1:{server.server_port}",
                   "VIDTRANS_ANIMATION_WORKER_TOKEN": "secret"}
            with patch.dict("os.environ", env):
                provider = GPUWorkerProvider(sleeper=lambda s: None)
            from infrastructure.video_providers import VideoRequest

            def cancelled() -> None:
                raise RuntimeError("cancelled by user")

            with self.assertRaises(RuntimeError):
                provider.generate(VideoRequest(keyframe=keyframe, prompt="walk", duration_seconds=3, width=64,
                                               height=36, fps=24, aspect_ratio="16:9", seed=1,
                                               idempotency_key="k-2"), Path(temp) / "out.mp4", cancelled)
            self.assertEqual(_WorkerHandler.paths, ["/v1/animate", "/v1/jobs/j1/cancel"])

    def test_veo_long_running_operation_is_polled_and_downloaded(self) -> None:
        calls = []

        class Response:
            def __init__(self, payload=None, data=b""):
                self.data = json.dumps(payload).encode() if payload is not None else data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, size=-1):
                chunk, self.data = (self.data, b"") if size < 0 else (self.data[:size], self.data[size:])
                return chunk

        def opener(request, timeout=None):
            calls.append((request.get_method(), request.full_url))
            if request.full_url.endswith(":predictLongRunning"):
                body = json.loads(request.data)
                self.assertEqual(body["parameters"]["durationSeconds"], 6)
                return Response({"name": "models/veo/operations/op1"})
            if request.full_url.endswith("operations/op1"):
                done = sum(1 for _m, url in calls if url.endswith("op1")) >= 2
                return Response({"done": done, "response": {"generateVideoResponse": {"generatedSamples": [
                    {"video": {"uri": "https://files.example/video.mp4"}}]}}} if done else {"done": False})
            return Response(data=b"MP4DATA" * 200)

        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"GEMINI_API_KEY": "k"}):
            keyframe = Path(temp) / "k.png"
            keyframe.write_bytes(b"png")
            provider = VeoProvider(opener=opener, sleeper=lambda s: None)
            from infrastructure.video_providers import VideoRequest

            result = provider.generate(VideoRequest(keyframe=keyframe, prompt="walk", duration_seconds=5.2,
                                                    width=1280, height=720, fps=24, aspect_ratio="16:9"),
                                       Path(temp) / "v.mp4", lambda: None)
            self.assertEqual((Path(temp) / "v.mp4").read_bytes(), b"MP4DATA" * 200)
            self.assertEqual(result.billed_seconds, 6.0)
            self.assertEqual(provider.generation_duration(9), 8.0)


if __name__ == "__main__":
    unittest.main()
