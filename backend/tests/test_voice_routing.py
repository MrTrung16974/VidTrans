import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from pipeline.voice_routing import (
    MAX_PITCH_SHIFT_SEMITONES,
    TTS_BASE_F0_HZ,
    PitchAnalysis,
    analyze_pitch,
    route_segments_by_pitch,
    tts_pitch_offset_hz,
)


def sine_wave(frequency: float, duration: float, sample_rate: int = 16000) -> np.ndarray:
    time = np.arange(int(duration * sample_rate), dtype=np.float32) / sample_rate
    return 0.45 * np.sin(2 * np.pi * frequency * time)


def voiced_wave(frequency: float, duration: float, sample_rate: int = 16000) -> np.ndarray:
    """Speech-like source: weak fundamental, strong 2nd/3rd harmonics, slight vibrato."""
    time = np.arange(int(duration * sample_rate), dtype=np.float32) / sample_rate
    phase = 2 * np.pi * np.cumsum(frequency * (1 + 0.02 * np.sin(2 * np.pi * 5 * time))) / sample_rate
    signal = 0.15 * np.sin(phase) + 0.35 * np.sin(2 * phase) + 0.25 * np.sin(3 * phase) + 0.1 * np.sin(4 * phase)
    noise = np.random.default_rng(7).normal(0, 0.01, len(time))
    return (0.6 * signal + noise).astype(np.float32)


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

    def test_harmonic_rich_voice_is_not_reported_an_octave_off(self) -> None:
        for frequency in (105.0, 140.0, 210.0, 260.0):
            analysis = analyze_pitch(voiced_wave(frequency, 1.2), 16000)
            self.assertAlmostEqual(analysis.pitch_hz or 0, frequency, delta=frequency * 0.04)
            self.assertGreaterEqual(analysis.confidence, 0.35)

    def test_quiet_recording_is_still_analysed(self) -> None:
        analysis = analyze_pitch(voiced_wave(130.0, 1.0) * 0.08, 16000)
        self.assertAlmostEqual(analysis.pitch_hz or 0, 130.0, delta=6.0)
        self.assertEqual(analysis.voice_type, "male")

    def test_hum_and_noise_are_not_mistaken_for_a_voice(self) -> None:
        time = np.arange(16000, dtype=np.float32) / 16000
        hum = 0.05 * np.sin(2 * np.pi * 55 * time) + np.random.default_rng(1).normal(0, 0.01, 16000)
        analysis = analyze_pitch(hum.astype(np.float32), 16000)
        self.assertIsNone(analysis.voice_type)

    def test_pitch_offset_moves_partway_and_is_capped(self) -> None:
        self.assertEqual(tts_pitch_offset_hz("male", TTS_BASE_F0_HZ["male"]), 0)
        self.assertEqual(tts_pitch_offset_hz("female", None), 0)
        low = tts_pitch_offset_hz("male", 95.0)
        high = tts_pitch_offset_hz("female", 250.0)
        self.assertLess(low, 0)
        self.assertGreater(high, 0)
        # Only part of the gap, not the full 28 Hz difference.
        self.assertLess(abs(low), TTS_BASE_F0_HZ["male"] - 95.0)
        cap = TTS_BASE_F0_HZ["female"] * (2 ** (MAX_PITCH_SHIFT_SEMITONES / 12) - 1)
        self.assertLessEqual(tts_pitch_offset_hz("female", 400.0), round(cap) + 2)
        self.assertEqual(tts_pitch_offset_hz("male", 116.0) % 2, 0)

    def test_one_speaker_turn_gets_one_stable_pitch_offset(self) -> None:
        samples = np.zeros(6 * 16000, dtype=np.float32)
        analyses = [
            PitchAnalysis(98.0, 0.8, 20, 20, "male"),
            PitchAnalysis(104.0, 0.7, 20, 20, "male"),
            PitchAnalysis(None, 0.0, 0, 20, None),
            PitchAnalysis(101.0, 0.9, 20, 20, "male"),
            PitchAnalysis(150.0, 0.8, 20, 20, "male"),
            PitchAnalysis(240.0, 0.9, 20, 20, "female"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "turns.wav"
            self.write_audio(audio_path, samples)
            segments = [{"start": float(i), "end": float(i + 1), "text": str(i)} for i in range(6)]
            with patch("pipeline.voice_routing.analyze_pitch", side_effect=analyses):
                route_segments_by_pitch(segments, audio_path, fallback_voice="female")

        offsets = [segment["voice_pitch_offset_hz"] for segment in segments]
        # First speaker (incl. the silent cue) shares one value; the 150 Hz cue
        # is a different male speaker; the female line is matched separately.
        self.assertEqual(len(set(offsets[:4])), 1)
        self.assertLess(offsets[0], 0)
        self.assertGreater(offsets[4], offsets[0])
        self.assertGreater(offsets[5], 0)
        self.assertEqual(segments[0]["voice_routing"]["matched_pitch_hz"], 101.0)

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

    def test_weak_but_clear_pitch_beats_the_video_majority(self) -> None:
        samples = np.zeros(4 * 16000, dtype=np.float32)
        analyses = [
            PitchAnalysis(230.0, 0.9, 20, 20, "female"),
            PitchAnalysis(240.0, 0.9, 20, 20, "female"),
            PitchAnalysis(250.0, 0.9, 20, 20, "female"),
            PitchAnalysis(110.0, 0.28, 8, 20, None),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "weak.wav"
            self.write_audio(audio_path, samples)
            segments = [{"start": float(i), "end": float(i + 1), "text": str(i)} for i in range(4)]
            with patch("pipeline.voice_routing.analyze_pitch", side_effect=analyses):
                route_segments_by_pitch(segments, audio_path, fallback_voice="female")

        self.assertEqual(segments[3]["voice_type"], "male")
        self.assertEqual(segments[3]["voice_routing"]["selection_source"], "weak_pitch")

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
