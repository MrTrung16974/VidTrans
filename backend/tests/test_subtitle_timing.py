from __future__ import annotations

import unittest

from pipeline.subtitle_timing import (
    SUBTITLE_TAIL_HOLD_SECONDS,
    SUBTITLE_TRANSITION_GAP_SECONDS,
    subtitle_render_window,
)


class SubtitleTimingTests(unittest.TestCase):
    def test_starts_with_source_cue_even_when_voice_drifts_later(self) -> None:
        segments = [
            {
                "start": 1.0,
                "end": 2.0,
                "tts_start": 1.35,
                "tts_end": 2.55,
                "text": "Xin chào",
            }
        ]

        start, end = subtitle_render_window(segments, 0)

        self.assertEqual(start, 1.0)
        self.assertAlmostEqual(end, 2.55 + SUBTITLE_TAIL_HOLD_SECONDS)

    def test_ocr_caption_follows_visible_source_window_exactly(self) -> None:
        segments = [
            {
                "start": 1.0,
                "end": 2.0,
                "tts_start": 1.35,
                "tts_end": 2.55,
                "text": "Xin chào",
                "source_method": "ocr",
            },
            {"start": 3.0, "end": 4.0, "text": "Tạm biệt", "source_method": "ocr"},
        ]

        self.assertEqual(subtitle_render_window(segments, 0), (1.0, 2.0))

    def test_voice_overrun_never_covers_next_source_caption(self) -> None:
        segments = [
            {"start": 0.0, "end": 1.0, "tts_start": 0.0, "tts_end": 1.6, "text": "Một"},
            {"start": 1.2, "end": 2.0, "tts_start": 1.62, "tts_end": 2.4, "text": "Hai"},
        ]

        first = subtitle_render_window(segments, 0)
        second = subtitle_render_window(segments, 1)

        self.assertAlmostEqual(first[1], 1.2 - SUBTITLE_TRANSITION_GAP_SECONDS)
        self.assertEqual(second[0], 1.2)

    def test_tail_hold_stops_before_next_visible_caption(self) -> None:
        segments = [
            {"start": 0.0, "end": 1.0, "tts_start": 0.0, "tts_end": 1.0, "text": "Một"},
            {"start": 1.1, "end": 2.0, "tts_start": 1.1, "tts_end": 2.0, "text": "Hai"},
        ]

        _, end = subtitle_render_window(segments, 0)

        self.assertAlmostEqual(end, 1.1 - SUBTITLE_TRANSITION_GAP_SECONDS)

    def test_skipped_caption_does_not_cut_tail_hold(self) -> None:
        segments = [
            {"start": 0.0, "end": 1.0, "text": "Một"},
            {"start": 1.05, "end": 1.5, "text": "Bỏ", "skip_subtitle": True},
            {"start": 2.0, "end": 3.0, "text": "Hai"},
        ]

        _, end = subtitle_render_window(segments, 0)

        self.assertAlmostEqual(end, 1.0 + SUBTITLE_TAIL_HOLD_SECONDS)


if __name__ == "__main__":
    unittest.main()
