import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from pipeline.voice_routing import PitchAnalysis, analyze_pitch, route_segments_by_pitch


def sine_wave(frequency: float, duration: float, sample_rate: int = 16000) -> np.ndarray:
    time = np.arange(int(duration * sample_rate), dtype=np.float32) / sample_rate
    return 0.45 * np.sin(2 * np.pi * frequency * time)


class VoiceRoutingTests(unittest.TestCase):
    @staticmethod
    def write_audio(path: Path, samples: np.ndarray, sample_rate: int = 16000) -> None:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes((samples * 32767).astype("<i2").tobytes())

    def test_identifies_low_and_high_pitch_voice_styles(self) -> None:
        low = analyze_pitch(sine_wave(125.0, 1.0), 16000)
        high = analyze_pitch(sine_wave(220.0, 1.0), 16000)

        self.assertAlmostEqual(low.pitch_hz or 0, 125.0, delta=8.0)
        self.assertEqual(low.voice_type, "male")
        self.assertAlmostEqual(high.pitch_hz or 0, 220.0, delta=8.0)
        self.assertEqual(high.voice_type, "female")

    def test_routes_each_segment_and_preserves_analysis_metadata(self) -> None:
        sample_rate = 16000
        samples = np.concatenate((sine_wave(125.0, 1.0), sine_wave(220.0, 1.0)))
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "analysis.wav"
            self.write_audio(audio_path, samples, sample_rate)

            segments = [
                {"start": 0.0, "end": 1.0, "text": "Một"},
                {"start": 1.0, "end": 2.0, "text": "Hai"},
            ]
            summary = route_segments_by_pitch(segments, audio_path, fallback_voice="female")

        self.assertEqual([segment["voice_type"] for segment in segments], ["male", "female"])
        self.assertEqual(segments[0]["voice_routing"]["selection_source"], "pitch")
        self.assertEqual(summary["male"], 1)
        self.assertEqual(summary["female"], 1)

    def test_silence_between_matching_voices_uses_neighbors(self) -> None:
        samples = np.concatenate((sine_wave(125.0, 1.0), np.zeros(16000), sine_wave(125.0, 1.0)))
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "neighbors.wav"
            self.write_audio(audio_path, samples)
            segments = [
                {"start": 0.0, "end": 1.0, "text": "Một"},
                {"start": 1.0, "end": 2.0, "text": "Hai"},
                {"start": 2.0, "end": 3.0, "text": "Ba"},
            ]
            route_segments_by_pitch(segments, audio_path, fallback_voice="female")

        self.assertEqual([segment["voice_type"] for segment in segments], ["male", "male", "male"])
        self.assertEqual(segments[1]["voice_routing"]["selection_source"], "neighbor")

    def test_weak_isolated_flip_is_smoothed_but_strong_switch_is_kept(self) -> None:
        samples = np.zeros(4 * 16000, dtype=np.float32)
        analyses = [
            PitchAnalysis(220.0, 0.9, 20, 20, "female"),
            PitchAnalysis(150.0, 0.4, 10, 20, "male"),
            PitchAnalysis(225.0, 0.9, 20, 20, "female"),
            PitchAnalysis(130.0, 0.9, 20, 20, "male"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "smoothing.wav"
            self.write_audio(audio_path, samples)
            segments = [
                {"start": float(i), "end": float(i + 1), "text": str(i)} for i in range(4)
            ]
            with patch("pipeline.voice_routing.analyze_pitch", side_effect=analyses):
                route_segments_by_pitch(segments, audio_path, fallback_voice="female")

        self.assertEqual([segment["voice_type"] for segment in segments], ["female", "female", "female", "male"])
        self.assertEqual(segments[1]["voice_routing"]["selection_source"], "smoothed")
        self.assertEqual(segments[3]["voice_routing"]["selection_source"], "pitch")

    def test_all_unvoiced_segments_use_selected_fallback(self) -> None:
        samples = np.zeros(2 * 16000, dtype=np.float32)
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "silent.wav"
            self.write_audio(audio_path, samples)
            segments = [
                {"start": 0.0, "end": 1.0, "text": "Một"},
                {"start": 1.0, "end": 2.0, "text": "Hai"},
            ]
            summary = route_segments_by_pitch(segments, audio_path, fallback_voice="male")

        self.assertEqual([segment["voice_type"] for segment in segments], ["male", "male"])
        self.assertEqual(summary["fallback"], 2)
