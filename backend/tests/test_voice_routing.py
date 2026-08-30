import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from pipeline.voice_routing import analyze_pitch, route_segments_by_pitch


def sine_wave(frequency: float, duration: float, sample_rate: int = 16000) -> np.ndarray:
    time = np.arange(int(duration * sample_rate), dtype=np.float32) / sample_rate
    return 0.45 * np.sin(2 * np.pi * frequency * time)


class VoiceRoutingTests(unittest.TestCase):
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
            with wave.open(str(audio_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                output.writeframes((samples * 32767).astype("<i2").tobytes())

            segments = [
                {"start": 0.0, "end": 1.0, "text": "Một"},
                {"start": 1.0, "end": 2.0, "text": "Hai"},
            ]
            summary = route_segments_by_pitch(segments, audio_path, fallback_voice="female")

        self.assertEqual([segment["voice_type"] for segment in segments], ["male", "female"])
        self.assertEqual(segments[0]["voice_routing"]["selection_source"], "pitch")
        self.assertEqual(summary["male"], 1)
        self.assertEqual(summary["female"], 1)
