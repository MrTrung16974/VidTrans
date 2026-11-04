"""Decide, per stretch of a soundtrack, whether the original Whisper pass needs a second opinion.

Running the separation/enhancement branches over a whole video costs several Whisper passes
on CPU, and on a clean voice-over they only add risk. Instead the first pass and the audio
scene labels point at the stretches where recognition is likely wrong:

* ``speech_over_music``      – the classifier hears speech together with music or singing;
* ``low_confidence``         – Whisper's own word probabilities are low;
* ``missed_speech``          – the classifier is sure of speech but Whisper wrote nothing;
* ``possible_hallucination`` – Whisper wrote text where the classifier hears no voice at all;
* ``noisy``                  – the voice sits close to the noise floor (low SNR).

Only those stretches (padded, snapped to whole Whisper segments, merged) are cut out and
re-recognised; a clean video costs nothing extra. Each stretch gets the branches that fit
its problem: a vocal stem against music, speech enhancement against noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from pipeline.audio_scene import (
    CLEAR_NO_SPEECH,
    HOP_SECONDS,
    MUSIC_PRESENT,
    SINGING_PRESENT,
    SPEECH_PRESENT,
    WINDOW_SECONDS,
    SceneWindow,
    scene_scores,
)
from pipeline.multi_branch_asr import segment_confidence

# Mean word probability under which a Whisper segment is doubtful.
LOW_CONFIDENCE = 0.55
# avg_logprob under which a segment without word timings is doubtful.
LOW_AVG_LOGPROB = -0.8
# The classifier must be this sure of speech before an empty stretch counts as missed.
MISSED_SPEECH_PRESENT = 0.5
MIN_MISSED_SECONDS = 2.0
# Voice less than this many dB above the noise floor is noisy speech.
NOISY_SNR_DB = 15.0
REGION_PADDING = 1.0
MERGE_GAP = 1.5
# Past this share of the video, cutting clips costs more than one whole-file pass.
WHOLE_FILE_SHARE = 0.6

REASON_WEIGHTS = {
    "speech_over_music": 1.0,
    "low_confidence": 1.0,
    "missed_speech": 1.5,
    "possible_hallucination": 1.2,
}


@dataclass
class SuspectRegion:
    start: float
    end: float
    reasons: set[str] = field(default_factory=set)
    severity: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start

    def as_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3),
                "reasons": sorted(self.reasons), "severity": round(self.severity, 3)}


@dataclass
class TriagePlan:
    regions: list[SuspectRegion]
    whole_file: bool = False
    noisy: bool = False
    snr_db: float | None = None
    skipped_regions: int = 0
    # Exact lines (not whole regions) Whisper wrote where the classifier heard no voice.
    hallucination_spans: list[tuple[float, float]] = field(default_factory=list)

    @property
    def needed(self) -> bool:
        return bool(self.regions)

    def branches_for(self, region: SuspectRegion) -> tuple[bool, bool]:
        """(separate vocals, enhance speech) for one region."""
        enhance = self.noisy or "noisy" in region.reasons
        separate = bool(region.reasons - {"noisy"}) or not enhance
        return separate, enhance

    def summary(self) -> dict[str, Any]:
        return {
            "whole_file": self.whole_file,
            "noisy": self.noisy,
            "snr_db": None if self.snr_db is None else round(self.snr_db, 1),
            "regions": [region.as_dict() for region in self.regions],
            "checked_seconds": round(sum(region.duration for region in self.regions), 1),
            "skipped_regions": self.skipped_regions,
        }


def _window_core(window: SceneWindow, last_end: float) -> tuple[float, float]:
    """The centre of a window that its neighbours do not also cover."""
    span = window.end - window.start
    margin = 0.0 if span < WINDOW_SECONDS else (span - HOP_SECONDS) / 2
    start = window.start + (margin if window.start > 0 else 0.0)
    end = window.end - (margin if window.end < last_end else 0.0)
    return start, end


def _overlaps(start: float, end: float, spans: Sequence[tuple[float, float]], minimum: float = 0.0) -> bool:
    return any(min(end, s_end) - max(start, s_start) > minimum for s_start, s_end in spans)


def _is_doubtful(segment: dict[str, Any]) -> tuple[bool, float]:
    confidence = segment_confidence(segment)
    if segment.get("words"):
        return confidence < LOW_CONFIDENCE, confidence
    logprob = segment.get("avg_logprob")
    return logprob is not None and float(logprob) < LOW_AVG_LOGPROB, confidence


def estimate_snr_db(
    samples: np.ndarray | None, rate: int, speech_spans: Sequence[tuple[float, float]], frame_seconds: float = 0.02
) -> float | None:
    """Loud speech frames (90th pct inside speech) over the noise floor (10th pct overall)."""
    if samples is None or len(samples) < rate or not speech_spans:
        return None
    frame = max(1, int(rate * frame_seconds))
    count = len(samples) // frame
    frames = np.asarray(samples[: count * frame], dtype=np.float32).reshape(count, frame)
    levels = 10 * np.log10(np.mean(frames * frames, axis=1) + 1e-12)
    audible = levels > -80.0  # digital silence (padding, muted intros) is not the noise floor
    if audible.sum() < 10:
        return None
    times = (np.arange(count) + 0.5) * frame_seconds
    in_speech = np.zeros(count, dtype=bool)
    for start, end in speech_spans:
        in_speech |= (times >= start) & (times < end)
    speech_levels = levels[in_speech & audible]
    if len(speech_levels) < 10:
        return None
    return float(np.percentile(speech_levels, 90) - np.percentile(levels[audible], 10))


def plan_asr_triage(
    *,
    scenes: Sequence[SceneWindow],
    segments: Sequence[dict[str, Any]],
    duration: float | None,
    muted: Sequence[tuple[float, float]] = (),
    samples: np.ndarray | None = None,
    rate: int = 16000,
    max_seconds: float = 300.0,
) -> TriagePlan:
    duration = float(duration or max((float(s["end"]) for s in segments), default=0.0))
    raw: list[SuspectRegion] = []
    hallucinations: list[tuple[float, float]] = []
    spoken = [(float(s["start"]), float(s["end"])) for s in segments if str(s.get("text") or "").strip()]

    last_end = scenes[-1].end if scenes else 0.0
    for window in scenes:
        start, end = _window_core(window, last_end)
        if end <= start:
            continue
        if window.speech >= SPEECH_PRESENT and (window.music >= MUSIC_PRESENT or window.singing >= SINGING_PRESENT):
            raw.append(SuspectRegion(start, end, {"speech_over_music"}, REASON_WEIGHTS["speech_over_music"]))
        if (
            window.speech >= MISSED_SPEECH_PRESENT
            and end - start >= MIN_MISSED_SECONDS
            and not _overlaps(start, end, spoken)
            and not _overlaps(start, end, muted)
        ):
            raw.append(SuspectRegion(start, end, {"missed_speech"}, REASON_WEIGHTS["missed_speech"]))

    for segment in segments:
        start, end = float(segment["start"]), float(segment["end"])
        doubtful, confidence = _is_doubtful(segment)
        if doubtful:
            weight = REASON_WEIGHTS["low_confidence"] + max(0.0, LOW_CONFIDENCE - confidence) * 2
            raw.append(SuspectRegion(start, end, {"low_confidence"}, weight))
        scores = scene_scores(scenes, start, end) if scenes else None
        if scores and scores["speech"] < CLEAR_NO_SPEECH and scores["singing"] < SINGING_PRESENT:
            raw.append(SuspectRegion(start, end, {"possible_hallucination"}, REASON_WEIGHTS["possible_hallucination"]))
            hallucinations.append((start, end))

    snr = estimate_snr_db(samples, rate, spoken)
    noisy = snr is not None and snr < NOISY_SNR_DB
    if noisy:
        for region in raw:
            region.reasons.add("noisy")

    regions = _merge(raw, spoken, duration)
    if not regions:
        return TriagePlan([], noisy=noisy, snr_db=snr)
    kept_hallucinations = lambda kept: [  # noqa: E731
        span for span in hallucinations if _overlaps(span[0], span[1], [(r.start, r.end) for r in kept])
    ]
    total = sum(region.duration for region in regions)
    if duration and total >= WHOLE_FILE_SHARE * duration and duration <= max_seconds:
        reasons = set().union(*(region.reasons for region in regions))
        whole = SuspectRegion(0.0, duration, reasons, sum(region.severity for region in regions))
        return TriagePlan([whole], whole_file=True, noisy=noisy, snr_db=snr, hallucination_spans=hallucinations)
    kept, skipped = _cap(regions, max_seconds)
    return TriagePlan(kept, noisy=noisy, snr_db=snr, skipped_regions=skipped,
                      hallucination_spans=kept_hallucinations(kept))


def _merge(
    raw: list[SuspectRegion], spoken: Sequence[tuple[float, float]], duration: float
) -> list[SuspectRegion]:
    """Pad, snap outwards to whole Whisper segments, and merge nearby regions."""
    expanded: list[SuspectRegion] = []
    for region in raw:
        start, end = region.start - REGION_PADDING, region.end + REGION_PADDING
        # A clip must never cut a recognised line in half, or the fused text loses words.
        changed = True
        while changed:
            changed = False
            for s_start, s_end in spoken:
                if s_start < end and s_end > start and (s_start < start or s_end > end):
                    start, end = min(start, s_start), max(end, s_end)
                    changed = True
        start = max(0.0, start)
        end = min(duration, end) if duration else end
        if end > start:
            expanded.append(SuspectRegion(start, end, set(region.reasons), region.severity))
    expanded.sort(key=lambda region: region.start)
    merged: list[SuspectRegion] = []
    for region in expanded:
        if merged and region.start <= merged[-1].end + MERGE_GAP:
            last = merged[-1]
            last.end = max(last.end, region.end)
            last.reasons |= region.reasons
            last.severity += region.severity
        else:
            merged.append(region)
    return merged


def _cap(regions: list[SuspectRegion], max_seconds: float) -> tuple[list[SuspectRegion], int]:
    """Keep the most severe regions (severity per second first) within the CPU budget."""
    ranked = sorted(regions, key=lambda region: region.severity / max(region.duration, 1.0), reverse=True)
    kept: list[SuspectRegion] = []
    used = 0.0
    for region in ranked:
        if used + region.duration <= max_seconds:
            kept.append(region)
            used += region.duration
    kept.sort(key=lambda region: region.start)
    return kept, len(regions) - len(kept)
