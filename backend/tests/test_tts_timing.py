from __future__ import annotations

import unittest

import numpy as np

from pipeline.tts_timing import (
    MAX_BORROWED_PAUSE_SECONDS,
    MAX_LEVEL_CORRECTION_DB,
    TARGET_SPEECH_RMS_DBFS,
    MAX_NATURAL_TEMPO,
    MAX_SYNTHESIS_RATE_BOOST_PERCENT,
    adaptive_synthesis_rate_percent,
    available_voice_window,
    bounded_tempo,
    schedule_voice_segments,
    speech_level_gain,
)


def tone(rms_dbfs: float, seconds: float = 1.0) -> np.ndarray:
    time = np.arange(int(24000 * seconds)) / 24000
    amplitude = np.sqrt(2) * 10 ** (rms_dbfs / 20)
    return (amplitude * np.sin(2 * np.pi * 180 * time)).astype(np.float32)


class TTSTimingTests(unittest.TestCase):
    def test_level_gain_matches_quiet_and_loud_clips_to_target(self) -> None:
        for level in (-21.0, -14.0):
            gain = speech_level_gain(tone(level))
            self.assertAlmostEqual(level + 20 * np.log10(gain), TARGET_SPEECH_RMS_DBFS, delta=0.2)

    def test_level_gain_ignores_pauses_and_is_bounded(self) -> None:
        with_pause = np.concatenate((tone(-20.0, 0.5), np.zeros(24000, dtype=np.float32), tone(-20.0, 0.5)))
        self.assertAlmostEqual(speech_level_gain(with_pause * 10 ** (3 / 20)), 1.0, delta=0.03)
        very_quiet = speech_level_gain(tone(-50.0))
        self.assertAlmostEqual(20 * np.log10(very_quiet), MAX_LEVEL_CORRECTION_DB, delta=0.01)
        self.assertEqual(speech_level_gain(np.zeros(100, dtype=np.float32)), 1.0)
        self.assertEqual(speech_level_gain(np.zeros(0, dtype=np.float32)), 1.0)

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
