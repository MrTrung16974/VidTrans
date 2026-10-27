from __future__ import annotations

import math
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np


MALE_MAX_F0_HZ = 160.0
FEMALE_MIN_F0_HZ = 190.0
MIN_CONFIDENCE = 0.35
STRONG_CONFIDENCE = 0.60
MIN_PITCH_HZ = 75.0
MAX_PITCH_HZ = 400.0
# YIN aperiodicity thresholds: below the first, the earliest dip is taken
# (avoids octave-down errors); above the second, the frame is unvoiced.
YIN_THRESHOLD = 0.20
YIN_MAX_APERIODICITY = 0.55
MIN_FRAME_RMS = 0.006
RELATIVE_FRAME_RMS = 0.35
MAX_RELATIVE_FRAME_RMS = 0.5
BACKGROUND_MARGIN = 1.6  # about +4 dB above the quietest fifth of the cue
MIN_VOICED_FRAMES = 3
# Even fluent speech is only partly voiced (consonants, breaths), so coverage
# is judged against this share of the loud frames, not against all frames.
EXPECTED_VOICED_RATIO = 0.6
# One speaker's intonation (incl. Chinese lexical tones) spans about this far
# either side of their centre; MAD beyond MAX_PITCH_SPREAD means no clear voice.
SPEAKER_WINDOW_SEMITONES = 4.0
MAX_PITCH_SPREAD_SEMITONES = 6.0

# Median F0 measured on vi-VN-NamMinhNeural / HoaiMyNeural at +0Hz. Pitch matching moves the
# TTS voice part of the way towards the source speaker, never all the way:
# neural voices turn metallic beyond a few semitones of their trained range.
TTS_BASE_F0_HZ = {"male": 123.0, "female": 206.0}
PITCH_MATCH_STRENGTH = 0.5
MAX_PITCH_SHIFT_SEMITONES = 2.5
PITCH_OFFSET_STEP_HZ = 2
# A jump larger than this inside one voice style is treated as a new speaker.
# Kept wide because one speaker's per-line median moves a lot with intonation.
SPEAKER_SPLIT_SEMITONES = 4.0
# Below MIN_CONFIDENCE a cue may still decide its own style when its pitch
# sits clearly inside one range; this beats copying the video's majority.
WEAK_CONFIDENCE = 0.2
# Dialogue cues (e.g. "em ... anh") count as this share of the video's speech
# time. Enough to settle a voice sitting between the pitch ranges, never
# enough to outvote a speaker whose pitch is clearly confident.
CONTENT_EVIDENCE_SHARE = 0.25
# Speaker turns of one voice style whose pitch centres lie within this distance
# are treated as one character. Narrower than SPEAKER_SPLIT_SEMITONES: a turn
# boundary needs a clear jump, a separate character only a stable difference.
SPEAKER_CLUSTER_SEMITONES = 2.5
# Pitch alone cannot tell many similar voices apart; beyond this, extra
# characters share the closest voice instead of inventing unreliable ones.
MAX_SPEAKERS_PER_VOICE = 3


@dataclass(frozen=True)
class PitchAnalysis:
    pitch_hz: float | None
    confidence: float
    voiced_frames: int
    total_frames: int
    voice_type: str | None


def read_pcm_wav(audio_path: Path) -> tuple[np.ndarray, int]:
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


def _estimate_frame_pitch(
    frame: np.ndarray, sample_rate: int, min_rms: float = MIN_FRAME_RMS
) -> tuple[float | None, float]:
    """YIN pitch estimate for one frame; returns (pitch_hz, periodicity)."""
    frame = frame - np.mean(frame)
    energy = float(np.sqrt(np.mean(frame * frame)))
    if energy < min_rms:
        return None, 0.0

    min_lag = max(2, int(sample_rate / MAX_PITCH_HZ))
    max_lag = int(sample_rate / MIN_PITCH_HZ)
    window = len(frame) - max_lag - 1
    if window < max_lag:
        return None, 0.0

    # Difference function d(tau) = E(0) + E(tau) - 2 r(tau), via FFT.
    size = 1 << int(math.ceil(math.log2(len(frame) + window)))
    spectrum = np.fft.rfft(frame, size)
    head_spectrum = np.fft.rfft(frame[:window], size)
    correlation = np.fft.irfft(spectrum * np.conj(head_spectrum), size)[: max_lag + 2]
    squares = np.concatenate(([0.0], np.cumsum(frame.astype(np.float64) ** 2)))
    lags = np.arange(max_lag + 2)
    shifted_energy = squares[lags + window] - squares[lags]
    difference = np.maximum(squares[window] + shifted_energy - 2.0 * correlation, 0.0)

    # Cumulative mean normalisation removes the bias towards long lags.
    cmndf = np.ones_like(difference)
    running = np.cumsum(difference[1:])
    cmndf[1:] = difference[1:] * np.arange(1, len(difference)) / np.maximum(running, 1e-12)

    region = cmndf[min_lag : max_lag + 1]
    below = np.flatnonzero(region < YIN_THRESHOLD)
    if below.size:
        lag = min_lag + int(below[0])
        while lag + 1 <= max_lag and cmndf[lag + 1] < cmndf[lag]:
            lag += 1
    else:
        lag = min_lag + int(np.argmin(region))
    aperiodicity = float(cmndf[lag])
    periodicity = max(0.0, 1.0 - aperiodicity)
    if aperiodicity > YIN_MAX_APERIODICITY:
        return None, periodicity

    # Parabolic interpolation gives sub-sample lag precision.
    refined = float(lag)
    if min_lag < lag < max_lag:
        left, centre, right = cmndf[lag - 1], cmndf[lag], cmndf[lag + 1]
        denominator = left - 2.0 * centre + right
        if denominator > 1e-12:
            refined = lag + 0.5 * (left - right) / denominator
    return sample_rate / refined, periodicity


def analyze_pitch(samples: np.ndarray, sample_rate: int) -> PitchAnalysis:
    """Estimate a voice-style signal from speech audio without external models."""
    frame_size = max(512, int(sample_rate * 0.064))
    hop_size = max(256, int(sample_rate * 0.032))
    starts = range(0, max(0, len(samples) - frame_size + 1), hop_size)
    frame_count = len(starts)
    if frame_count == 0:
        return PitchAnalysis(None, 0.0, 0, 0, None)
    # Gate relative to the cue's loud frames so quiet recordings still analyse.
    # Pauses between words expose the background bed (music, room tone), so
    # frames must also clear that floor; otherwise a sustained music note can
    # outvote the speaker. The cap keeps fully-voiced cues analysable.
    frame_rms = np.asarray(
        [float(np.sqrt(np.mean(np.square(samples[start : start + frame_size])))) for start in starts]
    )
    loud_level = float(np.percentile(frame_rms, 90))
    background_level = float(np.percentile(frame_rms, 20))
    min_rms = max(
        MIN_FRAME_RMS,
        min(MAX_RELATIVE_FRAME_RMS * loud_level,
            max(RELATIVE_FRAME_RMS * loud_level, BACKGROUND_MARGIN * background_level)),
    )
    loud_frames = int(np.count_nonzero(frame_rms >= min_rms))

    pitches: list[float] = []
    periodicities: list[float] = []
    weights: list[float] = []
    for start, rms in zip(starts, frame_rms):
        if rms < min_rms:
            continue
        pitch, periodicity = _estimate_frame_pitch(
            samples[start : start + frame_size], sample_rate, min_rms
        )
        if pitch is not None:
            pitches.append(pitch)
            periodicities.append(periodicity)
            weights.append(float(rms) * periodicity)

    if len(pitches) < MIN_VOICED_FRAMES:
        return PitchAnalysis(None, 0.0, len(pitches), frame_count, None)

    # Work in semitones: intonation (and Chinese lexical tone) moves pitch by
    # ratios, so a fixed Hz tolerance would treat high voices too harshly.
    semitones = 12.0 * np.log2(np.asarray(pitches, dtype=np.float64) / 100.0)
    frame_weights = np.asarray(weights)
    # The speaker is the pitch region carrying the most weight (loud, periodic
    # frames), not simply the majority: quieter background-music frames can
    # outnumber speech frames, and a plain median would then discard the voice.
    window_scores = [
        float(frame_weights[np.abs(semitones - value) <= SPEAKER_WINDOW_SEMITONES].sum())
        for value in semitones
    ]
    peak = float(semitones[int(np.argmax(window_scores))])
    stable = np.abs(semitones - peak) <= SPEAKER_WINDOW_SEMITONES
    stable_semitones = semitones[stable]
    centre = float(np.median(stable_semitones))
    mad = float(np.median(np.abs(stable_semitones - centre)))
    pitch_hz = _weighted_median(
        [pitches[i] for i in np.flatnonzero(stable)], frame_weights[stable].tolist()
    ) or float(np.median(pitches))
    coverage = min(1.0, int(np.count_nonzero(stable)) / max(loud_frames, 1) / EXPECTED_VOICED_RATIO)
    periodicity = float(np.median(np.asarray(periodicities)[stable]))
    stability = max(0.0, 1.0 - min(1.0, mad / MAX_PITCH_SPREAD_SEMITONES))
    confidence = float(coverage * periodicity * stability)
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
    content_voice: str | None = None,
) -> dict[str, int]:
    """Assign each subtitle cue a free local TTS voice choice.

    The classifier selects a TTS *voice style*, not a person's gender. Ambiguous
    or noisy cues inherit the latest confident style, then the narrator's
    voice implied by the dialogue (``content_voice``), then the fallback.
    """
    samples, sample_rate = read_pcm_wav(audio_path)
    analyses: list[PitchAnalysis] = []
    durations: list[float] = []
    for segment in segments:
        start = max(0.0, float(segment["start"]))
        end = max(start, float(segment["end"]))
        start_sample = max(0, int(start * sample_rate))
        end_sample = min(len(samples), int(end * sample_rate))
        analyses.append(analyze_pitch(samples[start_sample:end_sample], sample_rate))
        durations.append(max(0.0, end - start))

    evidence = {"male": 0.0, "female": 0.0}
    for analysis, duration in zip(analyses, durations):
        if analysis.voice_type:
            evidence[analysis.voice_type] += analysis.confidence * max(duration, 0.25)
    if content_voice in evidence:
        evidence[content_voice] += CONTENT_EVIDENCE_SHARE * sum(durations)
    global_voice: str | None = None
    if evidence["male"] > evidence["female"]:
        global_voice = "male"
    elif evidence["female"] > evidence["male"]:
        global_voice = "female"

    raw_voices = [analysis.voice_type for analysis in analyses]
    resolved_voices: list[str] = []
    sources: list[str] = []
    for index, analysis in enumerate(analyses):
        if analysis.voice_type:
            resolved_voices.append(analysis.voice_type)
            sources.append("pitch")
            continue
        previous_confident = next(
            (voice for voice in reversed(raw_voices[:index]) if voice is not None), None
        )
        next_confident = next((voice for voice in raw_voices[index + 1 :] if voice is not None), None)
        weak_voice = _weak_voice_type(analysis)
        if previous_confident and previous_confident == next_confident:
            resolved_voices.append(previous_confident)
            sources.append("neighbor")
        elif weak_voice:
            resolved_voices.append(weak_voice)
            sources.append("weak_pitch")
        elif global_voice:
            resolved_voices.append(global_voice)
            sources.append("content" if global_voice == content_voice else "global")
        elif previous_confident or next_confident:
            resolved_voices.append(previous_confident or next_confident or fallback_voice)
            sources.append("neighbor")
        else:
            resolved_voices.append(fallback_voice)
            sources.append("fallback")

    # Remove only a weak, isolated flip. Strong evidence and sustained speaker
    # changes remain untouched so real dialogue can still switch voice style.
    for index in range(1, len(resolved_voices) - 1):
        if (
            resolved_voices[index - 1] == resolved_voices[index + 1]
            and resolved_voices[index] != resolved_voices[index - 1]
            and analyses[index].confidence < STRONG_CONFIDENCE
        ):
            resolved_voices[index] = resolved_voices[index - 1]
            sources[index] = "smoothed"

    matched_pitches = _speaker_pitch_targets(analyses, durations, resolved_voices)
    speaker_ranks = assign_speaker_ranks(resolved_voices, matched_pitches, durations)

    summary = {"male": 0, "female": 0, "fallback": 0}
    for segment, analysis, selected_voice, selection_source, matched_pitch, speaker_rank in zip(
        segments, analyses, resolved_voices, sources, matched_pitches, speaker_ranks
    ):
        if selection_source != "pitch":
            summary["fallback"] += 1
        summary[selected_voice] += 1
        pitch_offset = tts_pitch_offset_hz(selected_voice, matched_pitch)
        segment["voice_type"] = selected_voice
        segment["voice_pitch_offset_hz"] = pitch_offset
        segment["speaker_rank"] = speaker_rank
        segment["voice_routing"] = {
            "mode": "auto",
            "speaker_id": f"{selected_voice}-{speaker_rank}",
            "selection_source": selection_source,
            "raw_voice_type": analysis.voice_type,
            "resolved_voice_type": selected_voice,
            "global_voice_type": global_voice,
            "content_voice_type": content_voice,
            "matched_pitch_hz": round(matched_pitch, 1) if matched_pitch else None,
            "pitch_offset_hz": pitch_offset,
            **asdict(analysis),
        }
    return summary


def _weak_voice_type(analysis: PitchAnalysis) -> str | None:
    if analysis.pitch_hz is None or analysis.confidence < WEAK_CONFIDENCE:
        return None
    if analysis.pitch_hz <= MALE_MAX_F0_HZ:
        return "male"
    if analysis.pitch_hz >= FEMALE_MIN_F0_HZ:
        return "female"
    return None


def _weighted_median(values: list[float], weights: list[float]) -> float | None:
    if not values:
        return None
    order = np.argsort(values)
    sorted_values = np.asarray(values, dtype=np.float64)[order]
    cumulative = np.cumsum(np.asarray(weights, dtype=np.float64)[order])
    return float(sorted_values[int(np.searchsorted(cumulative, cumulative[-1] / 2.0))])


def _semitones(pitch_hz: float, reference_hz: float) -> float:
    return 12.0 * math.log2(pitch_hz / reference_hz)


def _speaker_pitch_targets(
    analyses: list[PitchAnalysis],
    durations: list[float],
    voices: list[str],
) -> list[float | None]:
    """Return one stable source F0 per speaker turn.

    Matching every cue to its own pitch would make the voice wobble from line
    to line. Consecutive cues of the same voice style form a run until the
    pitch jumps by more than SPEAKER_SPLIT_SEMITONES (a different speaker);
    every cue in a run shares the run's confidence-weighted median.
    """

    def evidence(index: int) -> float | None:
        analysis = analyses[index]
        if analysis.pitch_hz is None or analysis.confidence < MIN_CONFIDENCE:
            return None
        if analysis.voice_type not in (None, voices[index]):
            return None
        return analysis.pitch_hz

    def weight(index: int) -> float:
        return analyses[index].confidence * max(durations[index], 0.25)

    runs: list[list[int]] = []
    run_pitches: list[float] = []
    run_weights: list[float] = []
    for index, voice in enumerate(voices):
        pitch = evidence(index)
        reference = _weighted_median(run_pitches, run_weights)
        new_run = (
            not runs
            or voices[runs[-1][-1]] != voice
            or (pitch is not None and reference is not None
                and abs(_semitones(pitch, reference)) > SPEAKER_SPLIT_SEMITONES)
        )
        if new_run:
            runs.append([])
            run_pitches, run_weights = [], []
        runs[-1].append(index)
        if pitch is not None:
            run_pitches.append(pitch)
            run_weights.append(weight(index))

    global_by_voice: dict[str, float | None] = {}
    for voice in set(voices):
        indices = [i for i, v in enumerate(voices) if v == voice and evidence(i) is not None]
        global_by_voice[voice] = _weighted_median(
            [evidence(i) for i in indices], [weight(i) for i in indices]  # type: ignore[misc]
        )

    targets: list[float | None] = [None] * len(voices)
    for run in runs:
        indices = [i for i in run if evidence(i) is not None]
        target = _weighted_median([evidence(i) for i in indices], [weight(i) for i in indices])  # type: ignore[misc]
        if target is None:
            target = global_by_voice.get(voices[run[0]])
        for index in run:
            targets[index] = target
    return targets


def assign_speaker_ranks(
    voices: list[str],
    pitches: list[float | None],
    durations: list[float],
) -> list[int]:
    """Group speaker turns into characters per voice style, ranked by speech time.

    Rank 0 is the character speaking most in that style and keeps the voice the
    user selected; higher ranks get the next voices of the same style. Cues
    without a pitch target stay with rank 0.
    """

    ranks = [0] * len(voices)
    for voice in set(voices):
        indices = [i for i, v in enumerate(voices) if v == voice and pitches[i]]
        weight_by_pitch: dict[float, float] = {}
        for i in indices:
            weight_by_pitch[pitches[i]] = weight_by_pitch.get(pitches[i], 0.0) + max(durations[i], 0.25)  # type: ignore[index]
        # Heaviest turns seed the clusters so a brief outlier cannot become the main voice.
        centres: list[float] = []
        cluster_weight: list[float] = []
        for pitch, weight in sorted(weight_by_pitch.items(), key=lambda item: -item[1]):
            distances = [abs(_semitones(pitch, centre)) for centre in centres]
            nearest = min(range(len(centres)), key=distances.__getitem__) if centres else None
            if nearest is not None and (
                distances[nearest] <= SPEAKER_CLUSTER_SEMITONES or len(centres) >= MAX_SPEAKERS_PER_VOICE
            ):
                cluster_weight[nearest] += weight
            else:
                centres.append(pitch)
                cluster_weight.append(weight)
        if len(centres) < 2:
            continue
        order = sorted(range(len(centres)), key=lambda c: -cluster_weight[c])
        rank_of_cluster = {cluster: rank for rank, cluster in enumerate(order)}
        for i in indices:
            nearest = min(range(len(centres)), key=lambda c: abs(_semitones(pitches[i], centres[c])))  # type: ignore[arg-type]
            ranks[i] = rank_of_cluster[nearest]
    return ranks


def tts_pitch_offset_hz(voice_type: str, source_pitch_hz: float | None) -> int:
    """Edge TTS pitch offset that nudges the voice towards the source F0."""
    base = TTS_BASE_F0_HZ.get(voice_type)
    if not base or not source_pitch_hz or source_pitch_hz <= 0:
        return 0
    shift = PITCH_MATCH_STRENGTH * _semitones(source_pitch_hz, base)
    shift = max(-MAX_PITCH_SHIFT_SEMITONES, min(MAX_PITCH_SHIFT_SEMITONES, shift))
    offset = base * (2.0 ** (shift / 12.0) - 1.0)
    # Quantise so lines of one speaker land on the same value.
    return int(round(offset / PITCH_OFFSET_STEP_HZ) * PITCH_OFFSET_STEP_HZ)


def route_segments_manually(segments: list[dict[str, Any]], voice_type: str) -> dict[str, int]:
    summary = {"male": 0, "female": 0, "fallback": 0}
    for segment in segments:
        segment["voice_type"] = voice_type
        segment["speaker_rank"] = 0
        segment.pop("voice_pitch_offset_hz", None)
        segment["voice_routing"] = {"mode": "manual", "selection_source": "manual"}
        summary[voice_type] += 1
    return summary
