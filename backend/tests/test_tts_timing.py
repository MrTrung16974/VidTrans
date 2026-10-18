from __future__ import annotations

import unittest

from pipeline.tts_timing import (
    MAX_BORROWED_PAUSE_SECONDS,
    MAX_NATURAL_TEMPO,
    MAX_SYNTHESIS_RATE_BOOST_PERCENT,
    adaptive_synthesis_rate_percent,
    available_voice_window,
    bounded_tempo,
    schedule_voice_segments,
)


class TTSTimingTests(unittest.TestCase):
    def test_tempo_never_exceeds_natural_limit(self) -> None:
        self.assertEqual(bounded_tempo(1.0, 1.0), 1.0)
        self.assertAlmostEqual(bounded_tempo(1.08, 1.0), 1.08)
        self.assertEqual(bounded_tempo(3.0, 0.5), MAX_NATURAL_TEMPO)

    def test_native_tts_rate_absorbs_most_sync_correction(self) -> None:
        self.assertEqual(adaptive_synthesis_rate_percent(1.0, 1.0), 0)
        self.assertEqual(adaptive_synthesis_rate_percent(1.08, 1.0), 8)
        self.assertEqual(
            adaptive_synthesis_rate_percent(3.0, 0.5),
            MAX_SYNTHESIS_RATE_BOOST_PERCENT,
        )

    def test_available_window_borrows_real_pause_without_consuming_all_of_it(self) -> None:
        segments = [
            {"start": 0.0, "end": 1.0},
            {"start": 1.6, "end": 2.2},
        ]
        window = available_voice_window(segments, 0)
        self.assertGreater(window, 0.95)
        self.assertLess(window, 1.6)

    def test_available_window_caps_long_pause_and_does_not_borrow_overlap(self) -> None:
        long_pause = [{"start": 0.0, "end": 1.0}, {"start": 5.0, "end": 6.0}]
        overlapping = [{"start": 0.0, "end": 1.0}, {"start": 0.8, "end": 1.5}]
        self.assertAlmostEqual(
            available_voice_window(long_pause, 0), 0.95 + MAX_BORROWED_PAUSE_SECONDS
        )
        self.assertAlmostEqual(available_voice_window(overlapping, 0), 0.95)

    def test_schedule_prevents_overlap_and_resets_at_real_pause(self) -> None:
        segments = [
            {"start": 0.0, "tts_path": "a.wav", "tts_duration": 1.2},
            {"start": 0.8, "tts_path": "b.wav", "tts_duration": 0.7},
            {"start": 4.0, "tts_path": "c.wav", "tts_duration": 0.5},
        ]

        schedule = schedule_voice_segments(segments, video_duration=5.0)

        self.assertGreater(schedule[1]["start"], schedule[0]["end"])
        self.assertEqual(schedule[2]["start"], 4.0)
        self.assertGreater(segments[1]["tts_drift"], 0)

    def test_schedule_does_not_start_audio_after_video_end(self) -> None:
        segments = [
            {"start": 2.0, "tts_path": "a.wav", "tts_duration": 2.0},
            {"start": 2.5, "tts_path": "b.wav", "tts_duration": 1.0},
        ]
        schedule = schedule_voice_segments(segments, video_duration=3.0)
        self.assertEqual(len(schedule), 1)


if __name__ == "__main__":
    unittest.main()
