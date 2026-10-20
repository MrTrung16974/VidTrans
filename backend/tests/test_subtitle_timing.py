from __future__ import annotations

import unittest

from pipeline.subtitle_timing import (
    SUBTITLE_TAIL_HOLD_SECONDS,
    SUBTITLE_TRANSITION_GAP_SECONDS,
    subtitle_render_window,
)


class SubtitleTimingTests(unittest.TestCase):
    def test_follows_scheduled_voice_instead_of_source_cue(self) -> None:
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

        self.assertEqual(start, 1.35)
        self.assertAlmostEqual(end, 2.55 + SUBTITLE_TAIL_HOLD_SECONDS)

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
