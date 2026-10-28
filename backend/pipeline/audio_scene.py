"""Label stretches of a soundtrack as speech, singing or music.

An Audio Spectrogram Transformer trained on AudioSet scores overlapping
windows of the soundtrack. Whisper is a speech model: it misses or invents
sung lyrics, so the pipeline uses these labels to keep it away from pure
music, to decide when vocals should be separated from the accompaniment and
to stop melody pitch from being read as a change of speaker.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np


logger = logging.getLogger(__name__)

AST_REPOSITORY = "Xenova/ast-finetuned-audioset-10-10-0.4593"
AST_MODEL_FILE = "onnx/model_quantized.onnx"
SAMPLE_RATE = 16000
WINDOW_SECONDS = 10.0
HOP_SECONDS = 5.0
SPEECH_LABELS = ("Speech", "Conversation", "Narration, monologue")
SINGING_LABELS = ("Singing", "Male singing", "Female singing", "Child singing", "Choir")
MUSIC_LABELS = ("Music",)
# AudioSet scores of a Douyin clip: dialogue over a score stays above 0.7
# "Speech", while sung verses fall below 0.1 even when "Singing" stays low.
SPEECH_PRESENT = 0.3
CLEAR_NO_SPEECH = 0.15
MUSIC_PRESENT = 0.5
SINGING_PRESENT = 0.3


@dataclass(frozen=True)
class SceneWindow:
    start: float
    end: float
    speech: float
    singing: float
    music: float


def _fbank(samples: np.ndarray, mean: float, std: float, frames: int = 1024) -> np.ndarray:
    import torch
    import torchaudio

    waveform = torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32))[None]
    features = torchaudio.compliance.kaldi.fbank(
        waveform,
        htk_compat=True,
        sample_frequency=SAMPLE_RATE,
        use_energy=False,
        window_type="hanning",
        num_mel_bins=128,
        dither=0.0,
        frame_shift=10,
    )
    features = torch.nn.functional.pad(features, (0, 0, 0, max(0, frames - features.shape[0])))[:frames]
    # Normalisation used when the AST checkpoint was trained.
    return ((features - mean) / (std * 2)).numpy()


class AudioSceneClassifier:
    """Lazy, thread-safe wrapper around the quantised AST ONNX model."""

    def __init__(self, repository: str = AST_REPOSITORY, model_file: str = AST_MODEL_FILE) -> None:
        self._repository = repository
        self._model_file = model_file
        self._lock = threading.Lock()
        self._session: Any = None
        self._columns: dict[str, list[int]] = {}
        self._mean = 0.0
        self._std = 1.0

    def _load(self) -> Any:
        if self._session is None:
            import onnxruntime
            from huggingface_hub import hf_hub_download

            config = json.loads(open(hf_hub_download(self._repository, "config.json"), encoding="utf-8").read())
            preprocessor = json.loads(
                open(hf_hub_download(self._repository, "preprocessor_config.json"), encoding="utf-8").read()
            )
            index = {label: int(key) for key, label in config["id2label"].items()}
            self._columns = {
                "speech": [index[label] for label in SPEECH_LABELS if label in index],
                "singing": [index[label] for label in SINGING_LABELS if label in index],
                "music": [index[label] for label in MUSIC_LABELS if label in index],
            }
            self._mean = float(preprocessor["mean"])
            self._std = float(preprocessor["std"])
            options = onnxruntime.SessionOptions()
            threads = int(os.environ.get("VIDTRANS_AUDIO_SCENE_THREADS", "0") or 0)
            if threads > 0:
                options.intra_op_num_threads = threads
            self._session = onnxruntime.InferenceSession(
                hf_hub_download(self._repository, self._model_file),
                options,
                providers=["CPUExecutionProvider"],
            )
        return self._session

    def classify(self, samples: np.ndarray, check_active: Any = None) -> list[SceneWindow]:
        """Score overlapping windows of 16 kHz mono samples."""

        duration = len(samples) / SAMPLE_RATE
        if duration <= 0:
            return []
        windows: list[SceneWindow] = []
        with self._lock:
            session = self._load()
            name = session.get_inputs()[0].name
            start = 0.0
            while start < duration:
                if check_active:
                    check_active()
                end = min(duration, start + WINDOW_SECONDS)
                clip = samples[int(start * SAMPLE_RATE) : int(end * SAMPLE_RATE)]
                logits = session.run(None, {name: _fbank(clip, self._mean, self._std)[None]})[0][0]
                scores = 1.0 / (1.0 + np.exp(-logits))
                windows.append(
                    SceneWindow(
                        start=round(start, 3),
                        end=round(end, 3),
                        **{kind: float(scores[columns].max()) for kind, columns in self._columns.items()},
                    )
                )
                if end >= duration:
                    break
                start += HOP_SECONDS
        return windows


def scene_scores(windows: Sequence[SceneWindow], start: float, end: float) -> dict[str, float] | None:
    """Overlap-weighted scores of the windows covering [start, end)."""

    total = 0.0
    sums = {"speech": 0.0, "singing": 0.0, "music": 0.0}
    for window in windows:
        overlap = min(end, window.end) - max(start, window.start)
        if overlap <= 0:
            continue
        total += overlap
        sums["speech"] += window.speech * overlap
        sums["singing"] += window.singing * overlap
        sums["music"] += window.music * overlap
    if total <= 0:
        return None
    return {kind: value / total for kind, value in sums.items()}


def audio_type(scores: dict[str, float] | None) -> str | None:
    """Classify one stretch: speech, singing or music (no voice the model trusts)."""

    if not scores:
        return None
    if scores["singing"] >= SINGING_PRESENT and scores["singing"] >= scores["speech"]:
        return "singing"
    if scores["speech"] >= SPEECH_PRESENT:
        return "speech"
    if scores["music"] >= MUSIC_PRESENT:
        # Whisper still heard words here, so they are sung over the music.
        return "singing"
    return None


def label_segments(segments: list[dict[str, Any]], windows: Sequence[SceneWindow]) -> dict[str, int]:
    """Store ``audio_type`` and its scores on every cue; return counts per type."""

    counts: dict[str, int] = {}
    for segment in segments:
        scores = scene_scores(windows, float(segment["start"]), float(segment["end"]))
        kind = audio_type(scores)
        if scores:
            segment["audio_scene"] = {key: round(value, 3) for key, value in scores.items()}
        if kind:
            segment["audio_type"] = kind
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def music_only_regions(windows: Sequence[SceneWindow], *, padding: float = 1.0) -> list[tuple[float, float]]:
    """Stretches holding music and clearly no speech, which Whisper should not hear.

    A window only mutes its centre half: neighbouring windows overlap it, and
    trimming by ``padding`` keeps the first words of a line that starts at the
    edge of a song.
    """

    regions: list[tuple[float, float]] = []
    for window in windows:
        if window.speech >= CLEAR_NO_SPEECH or window.music < MUSIC_PRESENT:
            continue
        span = window.end - window.start
        margin = 0.0 if span < WINDOW_SECONDS else (span - HOP_SECONDS) / 2
        start = window.start + (margin if window.start > 0 else 0.0)
        end = window.end - (margin if window.end < windows[-1].end else 0.0)
        if regions and start <= regions[-1][1]:
            regions[-1] = (regions[-1][0], max(regions[-1][1], end))
        else:
            regions.append((start, end))
    trimmed = []
    for start, end in regions:
        start = start + padding if start > 0 else start
        end = end - padding if end < windows[-1].end else end
        if end - start >= 2.0:
            trimmed.append((round(start, 3), round(end, 3)))
    return trimmed


def music_under_speech(windows: Sequence[SceneWindow]) -> float:
    """Share of the speech windows that also carry music."""

    speech = [window for window in windows if window.speech >= SPEECH_PRESENT]
    if not speech:
        return 0.0
    return sum(1 for window in speech if window.music >= MUSIC_PRESENT) / len(speech)


def has_singing_or_music_only(windows: Sequence[SceneWindow]) -> bool:
    return any(
        window.singing >= SINGING_PRESENT
        or (window.speech < CLEAR_NO_SPEECH and window.music >= MUSIC_PRESENT)
        for window in windows
    )
