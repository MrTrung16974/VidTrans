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
        self.assertIn("anime comic", prompt)
        self.assertIn("long white hair", prompt)
        self.assertIn("no text", prompt)

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
        scene_requests = images.requests[2:]
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

    def test_character_change_redraws_only_scenes_with_that_character(self) -> None:
        images = RecordingImages()
        board = self.project.save_storyboard(storyboard(), bump=False)
        self.producer(images).preview_images(board)
        images.requests.clear()

        board.characters[1].appearance = "girl with short red hair"
        board = self.project.save_storyboard(board)
        self.producer(images).preview_images(board)

        captions = [r.caption for r in images.requests]
        self.assertEqual(captions, ["Lan", "Cảnh 2", "Cảnh 3"])

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
