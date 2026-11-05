"""AI music suggestions for a photo-post slideshow."""
import json
import tempfile
import unittest
from pathlib import Path

from application.music_suggestions import MAX_PROMPT_IMAGES, suggest_music
from infrastructure.llm_providers import LLMError

TRACKS = [
    {"track_id": "sad1", "title": "Mưa rơi", "category": "Buồn / Cảm động", "duration_seconds": 120.0},
    {"track_id": "edm1", "title": "Party night", "category": "Sôi động / EDM", "duration_seconds": 95.4},
    {"track_id": "lofi1", "title": "Coffee", "category": "Lofi / Thư giãn", "duration_seconds": 150.0},
]


class FakeLLM:
    name = "fake"

    def __init__(self, reply: str | Exception, accepts_images: bool = False) -> None:
        self.reply = reply
        self.accepts_images = accepts_images
        self.calls: list[dict] = []

    def generate_json(self, system, user, schema, **kwargs):
        self.calls.append({"user": user, **kwargs})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class MusicSuggestionTests(unittest.TestCase):
    def test_model_picks_library_tracks_and_unknown_ids_are_dropped(self) -> None:
        llm = FakeLLM(json.dumps({"mood": "vui", "suggestions": [
            {"track_id": "edm1", "reason": "Nhịp  nhanh   hợp tiệc"},
            {"track_id": "made-up", "reason": "x"},
            {"track_id": "edm1", "reason": "lặp"},
            {"track_id": "lofi1", "reason": "Nhẹ nhàng"},
        ]}))
        result = suggest_music(caption="Tiệc cuối tuần #party", image_paths=[], tracks=TRACKS, llm=llm)
        self.assertEqual(result.source, "fake")
        self.assertEqual(result.mood, "vui")
        self.assertEqual([i["track_id"] for i in result.items], ["edm1", "lofi1"])
        self.assertEqual(result.items[0]["reason"], "Nhịp nhanh hợp tiệc")
        self.assertEqual(result.items[0]["title"], "Party night")
        self.assertIn("Tiệc cuối tuần #party", llm.calls[0]["user"])
        self.assertIn("lofi1", llm.calls[0]["user"])

    def test_model_failure_falls_back_to_caption_mood(self) -> None:
        for reply in (LLMError("down"), "not json", json.dumps({"suggestions": [{"track_id": "nope", "reason": ""}]})):
            result = suggest_music(caption="Chia tay buồn quá", image_paths=[], tracks=TRACKS, llm=FakeLLM(reply))
            self.assertEqual(result.source, "heuristic")
            self.assertEqual([i["track_id"] for i in result.items], ["sad1"])

    def test_no_model_and_no_mood_gives_nothing(self) -> None:
        result = suggest_music(caption="IMG 1234", image_paths=[], tracks=TRACKS, llm=None)
        self.assertEqual(result.items, [])

    def test_empty_library_never_calls_the_model(self) -> None:
        llm = FakeLLM("{}")
        self.assertEqual(suggest_music(caption="x", image_paths=[], tracks=[], llm=llm).items, [])
        self.assertEqual(llm.calls, [])

    def test_images_go_only_to_a_model_that_reads_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = []
            for index in range(7):
                path = Path(temp) / f"{index}.jpg"
                path.write_bytes(b"JPEG%d" % index)
                paths.append(path)
            reply = json.dumps({"suggestions": [{"track_id": "sad1", "reason": "ok"}]})
            vision = FakeLLM(reply, accepts_images=True)
            suggest_music(caption="", image_paths=paths, tracks=TRACKS, llm=vision)
            images = vision.calls[0]["images"]
            self.assertEqual(len(images), MAX_PROMPT_IMAGES)
            self.assertEqual(images[0], ("image/jpeg", b"JPEG0"))
            text_only = FakeLLM(reply)
            suggest_music(caption="", image_paths=paths, tracks=TRACKS, llm=text_only)
            self.assertNotIn("images", text_only.calls[0])


if __name__ == "__main__":
    unittest.main()
