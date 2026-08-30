from __future__ import annotations

import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np


MALE_MAX_F0_HZ = 160.0
FEMALE_MIN_F0_HZ = 190.0
MIN_CONFIDENCE = 0.35


@dataclass(frozen=True)
class PitchAnalysis:
    pitch_hz: float | None
    confidence: float
    voiced_frames: int
    total_frames: int
    voice_type: str | None


def _read_pcm_wav(audio_path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(audio_path), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("Voice analysis requires 16-bit PCM WAV audio")
        sample_rate = source.getframerate()
        channels = source.getnchannels()
        raw = source.readframes(source.getnframes())
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, sample_rate


def _estimate_frame_pitch(frame: np.ndarray, sample_rate: int) -> tuple[float | None, float]:
    frame = frame - np.mean(frame)
    energy = float(np.sqrt(np.mean(frame * frame)))
    if energy < 0.012:
        return None, 0.0

    windowed = frame * np.hanning(len(frame))
    correlation = np.correlate(windowed, windowed, mode="full")[len(windowed) - 1 :]
    if correlation[0] <= 0:
        return None, 0.0

    min_lag = max(1, int(sample_rate / 320.0))
    max_lag = min(len(correlation) - 1, int(sample_rate / 75.0))
    if max_lag <= min_lag:
        return None, 0.0
    region = correlation[min_lag : max_lag + 1]
    peak_offset = int(np.argmax(region))
    peak_lag = min_lag + peak_offset
    periodicity = float(correlation[peak_lag] / correlation[0])
    if periodicity < 0.30:
        return None, periodicity
    return sample_rate / peak_lag, periodicity


def analyze_pitch(samples: np.ndarray, sample_rate: int) -> PitchAnalysis:
    """Estimate a voice-style signal from speech audio without external models."""
    frame_size = max(512, int(sample_rate * 0.064))
    hop_size = max(256, int(sample_rate * 0.032))
    pitches: list[float] = []
    periodicities: list[float] = []
    frame_count = 0
    for start in range(0, max(0, len(samples) - frame_size + 1), hop_size):
        frame_count += 1
        pitch, periodicity = _estimate_frame_pitch(samples[start : start + frame_size], sample_rate)
        if pitch is not None:
            pitches.append(pitch)
            periodicities.append(periodicity)

    if not pitches or frame_count == 0:
        return PitchAnalysis(None, 0.0, 0, frame_count, None)

    pitch_hz = float(np.median(pitches))
    confidence = float(len(pitches) / frame_count * np.median(periodicities))
    voice_type: str | None = None
    if confidence >= MIN_CONFIDENCE:
        if pitch_hz <= MALE_MAX_F0_HZ:
            voice_type = "male"
        elif pitch_hz >= FEMALE_MIN_F0_HZ:
            voice_type = "female"
    return PitchAnalysis(pitch_hz, confidence, len(pitches), frame_count, voice_type)


def route_segments_by_pitch(
    segments: list[dict[str, Any]],
    audio_path: Path,
    *,
    fallback_voice: str,
) -> dict[str, int]:
    """Assign each subtitle cue a free local TTS voice choice.

    The classifier selects a TTS *voice style*, not a person's gender. Ambiguous
    or noisy cues inherit the latest confident style, then the selected fallback.
    """
    samples, sample_rate = _read_pcm_wav(audio_path)
    previous_voice = fallback_voice
    summary = {"male": 0, "female": 0, "fallback": 0}
    for segment in segments:
        start_sample = max(0, int(float(segment["start"]) * sample_rate))
        end_sample = min(len(samples), int(float(segment["end"]) * sample_rate))
        analysis = analyze_pitch(samples[start_sample:end_sample], sample_rate)
        selected_voice = analysis.voice_type or previous_voice
        selection_source = "pitch" if analysis.voice_type else "fallback"
        if analysis.voice_type:
            previous_voice = analysis.voice_type
        else:
            summary["fallback"] += 1
        summary[selected_voice] += 1
        segment["voice_type"] = selected_voice
        segment["voice_routing"] = {
            "mode": "auto",
            "selection_source": selection_source,
            **asdict(analysis),
        }
    return summary


def route_segments_manually(segments: list[dict[str, Any]], voice_type: str) -> dict[str, int]:
    summary = {"male": 0, "female": 0, "fallback": 0}
    for segment in segments:
        segment["voice_type"] = voice_type
        segment["voice_routing"] = {"mode": "manual", "selection_source": "manual"}
        summary[voice_type] += 1
    return summary
