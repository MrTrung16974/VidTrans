"""AI slideshow plans are useful but never trusted without validation."""
import json
import unittest
from pathlib import Path

from application.photo_slideshow_planning import MOTIONS, STYLES, parse_plan, plan_slideshow
from infrastructure.llm_providers import LLMError

TRACKS = [
    {"track_id": "calm", "title": "Calm", "category": "Lofi / Thư giãn", "duration_seconds": 90},
    {"track_id": "party", "title": "Party", "category": "Sôi động / EDM", "duration_seconds": 80},
]


class FakeLLM:
    name = "fake-model"
    accepts_images = False

    def __init__(self, reply):
        self.reply = reply

    def generate_json(self, *_args, **_kwargs):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def model_reply():
    return json.dumps({
        "style": "dynamic", "mood": "vui", "summary": "Mở nhanh, kết nhẹ", "confidence": 0.9,
        "slides": [
            {"image_index": 2, "duration_seconds": 2, "motion": "pan_right", "transition": "crossfade",
             "transition_seconds": 0.4, "role": "hook"},
            {"image_index": 0, "duration_seconds": 4, "motion": "slow_zoom_in", "transition": "fade",
             "transition_seconds": 0.5, "role": "story"},
            {"image_index": 1, "duration_seconds": 3, "motion": "static", "transition": "cut", "role": "ending"},
        ],
        "music_suggestions": [
            {"track_id": "party", "reason": "Hợp nhịp vui", "score": 0.95},
            {"track_id": "made-up", "reason": "Không tồn tại", "score": 1},
        ],
    })


class PhotoSlideshowPlanningTests(unittest.TestCase):
    def test_model_can_reorder_images_and_only_known_music_survives(self):
        plan = plan_slideshow(caption="party", image_paths=[Path("0.jpg"), Path("1.jpg"), Path("2.jpg")],
                              tracks=TRACKS, llm=FakeLLM(model_reply()), requested_seconds=12)
        self.assertEqual(plan.source, "fake-model")
        self.assertEqual([item["image_index"] for item in plan.slides], [2, 0, 1])
        self.assertAlmostEqual(sum(item["duration_seconds"] for item in plan.slides), 12)
        self.assertEqual([item["track_id"] for item in plan.music_suggestions], ["party"])
        self.assertTrue(all(item["motion"] in MOTIONS for item in plan.slides))

    def test_keep_original_order_overrides_model_order(self):
        plan = plan_slideshow(caption="", image_paths=[Path("0.jpg"), Path("1.jpg"), Path("2.jpg")],
                              tracks=TRACKS, llm=FakeLLM(model_reply()), keep_original_order=True)
        self.assertEqual([item["image_index"] for item in plan.slides], [0, 1, 2])

    def test_invalid_or_failed_model_falls_back_to_safe_plan(self):
        invalid = json.dumps({"style": "hacker", "slides": [{"image_index": 99}], "music_suggestions": []})
        for reply in (invalid, "not json", LLMError("down")):
            plan = plan_slideshow(caption="du lịch Đà Lạt", image_paths=[Path("0.jpg"), Path("1.jpg")],
                                  tracks=TRACKS, llm=FakeLLM(reply))
            self.assertEqual(plan.source, "heuristic")
            self.assertEqual(plan.style, "travel")
            self.assertEqual([item["image_index"] for item in plan.slides], [0, 1])
            self.assertTrue(all(item["motion"] in MOTIONS for item in plan.slides))

    def test_preferred_style_and_target_are_bounded(self):
        plan = plan_slideshow(caption="", image_paths=[Path("0.jpg"), Path("1.jpg")], tracks=[], llm=None,
                              requested_seconds=999, preferred_style="product")
        self.assertIn(plan.style, STYLES)
        self.assertEqual(plan.style, "product")
        self.assertEqual(plan.target_seconds, 16)

    def test_submitted_plan_rejects_tampered_enum(self):
        data = json.loads(model_reply())
        data.update(version=1)
        data["slides"][0]["motion"] = "shell-expression"
        with self.assertRaisesRegex(ValueError, "enum"):
            parse_plan(data, image_count=3, tracks=TRACKS, target_seconds=12,
                       keep_original_order=False, source="submitted", strict=True)

    def test_submitted_plan_repeats_the_sequence_for_a_longer_user_duration(self):
        data = json.loads(model_reply())
        data.update(version=1)
        data["slides"] = data["slides"][:2]
        data["slides"][0]["image_index"] = 0
        data["slides"][1]["image_index"] = 1
        plan = parse_plan(data, image_count=2, tracks=TRACKS, target_seconds=30,
                          keep_original_order=False, source="submitted", strict=True)
        self.assertEqual([slide["image_index"] for slide in plan.slides], [0, 1, 0, 1])
        self.assertAlmostEqual(sum(slide["duration_seconds"] for slide in plan.slides), 30)
        self.assertTrue(all(1 <= slide["duration_seconds"] <= 8 for slide in plan.slides))


if __name__ == "__main__":
    unittest.main()
