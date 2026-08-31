import unittest

from pipeline.subtitle_layout import SubtitleLayoutOptions, apply_subtitle_layout


class SubtitleLayoutTests(unittest.TestCase):
    def test_matches_ocr_position_and_visual_height(self) -> None:
        result = apply_subtitle_layout(
            [
                {
                    "text": "Tôi sẵn sàng trở thành phiên bản mình mong muốn",
                    "source_bbox": {"x": 0.25, "y": 0.8, "width": 0.5, "height": 0.05},
                    "source_font_height": 0.05,
                }
            ],
            video_width=1920,
            video_height=1080,
            style={},
        )[0]["subtitle_layout"]

        self.assertEqual(result["position_source"], "ocr")
        self.assertAlmostEqual(result["x"], 960, delta=1)
        self.assertAlmostEqual(result["y"], 891, delta=2)
        self.assertGreaterEqual(result["font_size"], 45)
        self.assertTrue(result["mask_original"])

    def test_places_vietnamese_above_original(self) -> None:
        result = apply_subtitle_layout(
            [
                {
                    "text": "Nội dung tiếng Việt",
                    "source_bbox": {"x": 0.3, "y": 0.82, "width": 0.4, "height": 0.06},
                }
            ],
            video_width=1280,
            video_height=720,
            style={"placement_mode": "above_original", "position_gap": 10},
        )[0]["subtitle_layout"]
        self.assertLess(result["y"], 0.82 * 720)
        self.assertFalse(result["mask_original"])

    def test_uses_safe_fallback_without_ocr_bbox(self) -> None:
        result = apply_subtitle_layout(
            [{"text": "Subtitle nhận diện từ giọng nói"}],
            video_width=1080,
            video_height=1920,
            style={"font_size": 36, "margin_v": 100},
        )[0]["subtitle_layout"]
        self.assertEqual(result["position_source"], "fallback")
        self.assertEqual(result["x"], 540)
        self.assertFalse(result["mask_original"])

    def test_rejects_unknown_placement_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "placement_mode"):
            SubtitleLayoutOptions.from_style({"placement_mode": "unknown"})


if __name__ == "__main__":
    unittest.main()
