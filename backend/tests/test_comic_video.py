import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from domain.comic import ComicCharacter, ComicScene, ComicSettings, DialogueLine, Storyboard
from infrastructure.comic_project import ComicProject
from infrastructure.image_providers import ImageRequest, PlaceholderImageProvider
from pipeline.comic_video import (
    ComicRuntime,
    ComicVideoProducer,
    motion_expressions,
    remove_stale_assets,
    scene_image_prompt,
    split_cue_text,
    timed_cues,
)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
FONT = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSansCJKsc-Regular.otf"


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def duration(path):
    out = run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    return float(out.stdout)


class RecordingImages(PlaceholderImageProvider):
    """Placeholder output, but records requests and claims reference support."""

    supports_references = True
    cache_key = "recording-v1"

    def __init__(self):
        super().__init__(FONT)
        self.requests: list[ImageRequest] = []

    def generate(self, request, output_path):
        self.requests.append(request)
        super().generate(request, output_path)


def storyboard():
    return Storyboard(
        title="Rừng đêm",
        characters=[
            ComicCharacter(id="minh", name="Minh", gender="male", appearance="young man, short black hair"),
            ComicCharacter(id="lan", name="Lan", gender="female", appearance="girl with long white hair"),
        ],
        scenes=[
            ComicScene(scene_index=1, narration="Đêm hôm đó, Minh bước vào khu rừng.", characters=["minh"],
                       visual_prompt="Minh walks into a dark forest", camera_motion="slow_zoom_in"),
            ComicScene(scene_index=2, narration="Một bóng trắng lướt qua.", characters=["lan"],
                       dialogue=[DialogueLine(character="lan", text="Anh là ai?")],
                       visual_prompt="A pale girl appears on the path", camera_motion="pan_left"),
            ComicScene(scene_index=3, narration="Minh lùi lại.", characters=["minh", "lan"],
                       visual_prompt="Minh steps back in fear", camera_motion="static"),
        ],
    )


class HelperTests(unittest.TestCase):
    def test_cues_split_at_punctuation_and_share_the_part_duration(self) -> None:
        self.assertEqual(split_cue_text("Một hai ba, bốn năm sáu. Bảy!", 12), ["Một hai ba,", "bốn năm sáu.", "Bảy!"])
        long = split_cue_text("Ánh trăng lạnh lẽo xuyên qua tán lá rừng già", 30)
        self.assertEqual(len(long), 2)
        self.assertLess(abs(len(long[0]) - len(long[1])), 8)  # Balanced, no one-word orphan.
        cues = timed_cues([("Một hai. Ba bốn năm sáu bảy.", 1.0, 4.0)], 12)
        self.assertEqual(cues[0]["start"], 1.0)
        self.assertAlmostEqual(cues[-1]["end"], 4.0, places=2)

    def test_motion_expressions_cover_every_motion(self) -> None:
        for motion in ("slow_zoom_in", "slow_zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"):
            zoom, x, y = motion_expressions(motion, 90)
            self.assertTrue(zoom and x and y)
        self.assertEqual(motion_expressions("static", 90)[0], "1")

    def test_scene_prompt_carries_style_cast_and_no_text_rule(self) -> None:
        board = storyboard()
        prompt = scene_image_prompt(board.scenes[1], board, "9:16")
        self.assertIn("semi-realistic Chinese manhua", prompt)
        self.assertIn("long white hair", prompt)
        self.assertIn("no text", prompt)

    def test_manhua_prompt_targets_soft_semi_realistic_style(self) -> None:
        board = storyboard()
        board.style = "manhua"
        prompt = scene_image_prompt(board.scenes[1], board, "9:16")
        self.assertIn("semi-realistic Chinese manhua", prompt)
        self.assertIn("soft pastel palette", prompt)
        self.assertIn("not cartoonish", prompt)

    def test_asset_paths_cannot_escape_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = ComicProject(Path(temp) / "comic")
            (project.dir("images") / "a.png").write_bytes(b"x")
            self.assertIsNotNone(project.asset_path("images", "a.png"))
            for kind, name in (("images", "../story.txt"), ("history", "r0001.json"), ("images", ".a.png")):
                self.assertIsNone(project.asset_path(kind, name))

    def test_storyboard_revisions_are_snapshotted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = ComicProject(Path(temp) / "comic")
            board = project.save_storyboard(storyboard(), bump=False)
            board.scenes[0].narration = "Khác."
            self.assertEqual(project.save_storyboard(board).revision, 2)
            self.assertEqual(sorted(p.name for p in (project.root / "history").iterdir()),
                             ["r0001.json", "r0002.json"])


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg is required")
class ProducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = ComicProject(self.root / "comic")
        self.voices: list[tuple[str, str, int]] = []
        self.reports: list[tuple[str, float]] = []

    def synthesize(self, text, path, gender, rate, pitch, language):
        self.voices.append((text, gender, pitch))
        seconds = max(0.6, len(text) / 20)
        run([FFMPEG, "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-ac", "1", str(path)])

    def producer(self, images, **settings):
        runtime = ComicRuntime(FFMPEG, run, duration, self.synthesize, FONT,
                               report=lambda step, progress, detail: self.reports.append((step, progress)))
        return ComicVideoProducer(self.project, ComicSettings(aspect_ratio="16:9", **settings), images, runtime)

    def test_render_then_edit_regenerates_only_the_edited_scene(self) -> None:
        images = RecordingImages()
        board = self.project.save_storyboard(storyboard(), bump=False)
        video, srt = self.root / "out.mp4", self.root / "out.srt"

        manifest = self.producer(images).produce(board, video, srt)

        self.assertAlmostEqual(duration(video), manifest["duration"], delta=0.1)
        self.assertEqual(len(images.requests), 5)  # Two character references + three scenes.
        # Scenes are drawn concurrently, so compare by caption rather than call order.
        scene_requests = sorted(images.requests[2:], key=lambda r: r.caption)
        self.assertEqual([len(r.references) for r in scene_requests], [1, 1, 2])
        srt_text = srt.read_text(encoding="utf-8")
        self.assertIn("Anh là ai?", srt_text)
        self.assertIn("00:00:00,000 -->", srt_text)
        # Dialogue in a different gender from the female narrator uses its own voice.
        self.assertIn(("Đêm hôm đó, Minh bước vào khu rừng.", "female", 0), self.voices)
        self.assertIn(("Anh là ai?", "female", 6), self.voices)

        board.scenes[1].narration = "Một bóng trắng lướt qua rất nhanh."
        board = self.project.save_storyboard(board)
        images.requests.clear()
        self.voices.clear()

        manifest = self.producer(images).produce(board, video, srt)

        self.assertEqual(images.requests, [])  # Narration does not affect the picture.
        self.assertEqual([v[0] for v in self.voices], ["Một bóng trắng lướt qua rất nhanh."])
        self.assertEqual(manifest["cache"]["misses"], 2)  # Scene 2 audio + clip.
        self.assertEqual(manifest["revision"], 2)

    def test_cit_voices_narrator_and_characters_separately(self) -> None:
        calls = []

        def synthesize(text, path, gender, rate, pitch, language, **provider):
            calls.append((text, gender, provider))
            self.synthesize(text, path, gender, rate, pitch, language)

        voices = {"narrator": "Ngọc Huyền", "female": "Thu Trang", "male": "Minh Đức"}
        runtime = ComicRuntime(FFMPEG, run, duration, synthesize, FONT)
        settings = ComicSettings(aspect_ratio="16:9", tts_provider="cit", tts_voices=voices)
        producer = ComicVideoProducer(self.project, settings, PlaceholderImageProvider(FONT), runtime)
        board = self.project.save_storyboard(storyboard(), bump=False)
        producer.produce(board, self.root / "c.mp4", self.root / "c.srt")

        self.assertIn(("Đêm hôm đó, Minh bước vào khu rừng.", "female",
                       {"provider": "cit", "voice_key": "Ngọc Huyền"}), calls)
        # Same gender as the narrator, but a different CIT voice instead of a pitch lift.
        self.assertIn(("Anh là ai?", "female", {"provider": "cit", "voice_key": "Thu Trang"}), calls)

        # Switching back to Edge must not reuse CIT audio.
        self.voices.clear()
        self.producer(PlaceholderImageProvider(FONT)).produce(board, self.root / "e.mp4", self.root / "e.srt")
        self.assertIn(("Anh là ai?", "female", 6), self.voices)

    def test_character_change_redraws_only_scenes_with_that_character(self) -> None:
        images = RecordingImages()
        board = self.project.save_storyboard(storyboard(), bump=False)
        self.producer(images).preview_images(board)
        images.requests.clear()

        board.characters[1].appearance = "girl with short red hair"
        board = self.project.save_storyboard(board)
        self.producer(images).preview_images(board)

        captions = [r.caption for r in images.requests]
        self.assertEqual(captions[0], "Lan")  # References are drawn before the scenes that use them.
        self.assertCountEqual(captions[1:], ["Cảnh 2", "Cảnh 3"])

    def test_parallel_render_matches_sequential_and_shares_repeated_lines(self) -> None:
        board = storyboard()
        for scene in board.scenes:
            scene.dialogue = [DialogueLine(character="lan", text="Anh là ai?")]
        images = PlaceholderImageProvider(FONT)
        sequential = self.producer(images)
        sequential.workers = 1
        first = sequential.produce(board, self.root / "a.mp4", self.root / "a.srt")
        shutil.rmtree(self.project.root / "audio")
        shutil.rmtree(self.project.root / "clips")
        self.voices.clear()
        self.reports.clear()

        second = self.producer(images).produce(board, self.root / "b.mp4", self.root / "b.srt")

        self.assertEqual(self.voices.count(("Anh là ai?", "female", 6)), 1)  # Built once, reused by all three.
        self.assertEqual(second["scenes"], first["scenes"])
        self.assertEqual((self.root / "b.srt").read_text("utf-8"), (self.root / "a.srt").read_text("utf-8"))
        progress = [value for step, value in self.reports if step == "comic_clips"]
        self.assertEqual(progress, sorted(progress))

    def test_uploaded_override_replaces_the_generated_image(self) -> None:
        from PIL import Image

        images = RecordingImages()
        Image.new("RGB", (400, 300), (200, 10, 10)).save(self.project.dir("uploads") / "s001-upload.png")
        board = storyboard()
        board.scenes[0].image_override = "s001-upload.png"
        manifest = self.producer(images, burn_subtitles=False).produce(board, self.root / "o.mp4", self.root / "o.srt")
        self.assertNotIn("Cảnh 1", [r.caption for r in images.requests])
        with Image.open(self.project.root / "images" / manifest["scenes"]["1"]["image"]) as image:
            self.assertEqual(image.size, (1920, 1080))
            self.assertEqual(image.getpixel((960, 540)), (200, 10, 10))

    def test_background_music_is_mixed_and_stale_clips_are_removed(self) -> None:
        music = self.root / "music.mp3"
        run([FFMPEG, "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=2", str(music)])
        board = storyboard()
        board.scenes = board.scenes[:1]
        producer = self.producer(PlaceholderImageProvider(FONT))
        producer.produce(board, self.root / "a.mp4", self.root / "a.srt")
        board.scenes[0].camera_motion = "pan_up"
        manifest = producer.produce(board, self.root / "b.mp4", self.root / "b.srt", music)
        remove_stale_assets(self.project, manifest)
        self.assertEqual([p.name for p in (self.project.root / "clips").iterdir()], [manifest["scenes"]["1"]["clip"]])
        streams = run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json",
                       str(self.root / "b.mp4")]).stdout
        self.assertEqual(sorted(s["codec_type"] for s in json.loads(streams)["streams"]), ["audio", "video"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg is required")
class ImageQuotaFallbackTests(unittest.TestCase):
    """Regression: job d5395480 failed outright when Gemini Image answered 429."""

    def test_out_of_quota_images_become_stand_ins_and_are_retried_next_render(self) -> None:
        from infrastructure.image_providers import ImageGenerationError

        class FlakyGemini(PlaceholderImageProvider):
            key = "gemini"
            cache_key = "gemini-test"
            supports_references = True

            def __init__(self):
                super().__init__(FONT)
                self.calls = 0
                self.down = True

            def generate(self, request, output_path):
                self.calls += 1
                if self.down:
                    error = ImageGenerationError("Gemini Image hết hạn mức (HTTP 429)")
                    error.rate_limited = error.unavailable = True
                    raise error
                super().generate(request, output_path)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = ComicProject(root / "comic")
            synth = lambda text, path, *a: run([FFMPEG, "-y", "-f", "lavfi", "-i", "sine=duration=0.6", str(path)])
            runtime = ComicRuntime(FFMPEG, run, duration, synth, FONT)
            images = FlakyGemini()
            board = storyboard()

            manifest = ComicVideoProducer(project, ComicSettings(aspect_ratio="16:9", quality="draft"), images,
                                          runtime).produce(board, root / "a.mp4", root / "a.srt")

            self.assertEqual(images.calls, 1)  # Marked down after the first 429, not retried per scene.
            self.assertEqual(manifest["image_fallback"]["count"], 3)
            self.assertIn("429", manifest["image_fallback"]["reason"])
            self.assertTrue(all(entry["image"].endswith("-fallback.png") for entry in manifest["scenes"].values()))
            self.assertEqual(manifest["references"], {})  # A stand-in is never used as a character reference.

            images.down = False
            manifest = ComicVideoProducer(project, ComicSettings(aspect_ratio="16:9", quality="draft"), images,
                                          runtime).produce(board, root / "b.mp4", root / "b.srt")
            self.assertIsNone(manifest["image_fallback"])
            self.assertFalse(any(entry["image"].endswith("-fallback.png") for entry in manifest["scenes"].values()))

    def test_prompt_rejection_only_affects_that_scene(self) -> None:
        from infrastructure.image_providers import ImageGenerationError

        class PickyGemini(PlaceholderImageProvider):
            key = "gemini"
            cache_key = "gemini-test"

            def generate(self, request, output_path):
                if request.caption == "Cảnh 2":
                    raise ImageGenerationError("Gemini không trả về ảnh (SAFETY)")
                super().generate(request, output_path)

        with tempfile.TemporaryDirectory() as temp:
            project = ComicProject(Path(temp) / "comic")
            runtime = ComicRuntime(FFMPEG, run, duration, lambda *a: None, FONT)
            manifest = ComicVideoProducer(project, ComicSettings(quality="draft"), PickyGemini(FONT),
                                          runtime).preview_images(storyboard())
            images = {index: entry["image"] for index, entry in manifest["scenes"].items()}
            self.assertTrue(images["2"].endswith("-fallback.png"))
            self.assertFalse(images["3"].endswith("-fallback.png"))
