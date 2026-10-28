"""Speaker embeddings for telling characters apart.

Pitch says whether a voice is low or high, not whose voice it is: one person
singing or shouting moves several semitones, while two men may share a pitch.
A WeSpeaker ResNet34 (VoxCeleb) embedding captures the timbre instead.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import numpy as np


WESPEAKER_REPOSITORY = "Wespeaker/wespeaker-voxceleb-resnet34-LM"
WESPEAKER_MODEL_FILE = "voxceleb_resnet34_LM.onnx"
SAMPLE_RATE = 16000
# Embeddings of shorter clips barely resemble each other even for one speaker
# (cosine 0.1-0.3 on 1 s Douyin lines), so such cues are not embedded.
MIN_EMBEDDING_SECONDS = 2.0
MAX_EMBEDDING_SECONDS = 20.0


class SpeakerEmbedder:
    def __init__(self, repository: str = WESPEAKER_REPOSITORY, model_file: str = WESPEAKER_MODEL_FILE) -> None:
        self._repository = repository
        self._model_file = model_file
        self._session: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._session is None:
            import onnxruntime
            from huggingface_hub import hf_hub_download

            options = onnxruntime.SessionOptions()
            threads = int(os.environ.get("VIDTRANS_SPEAKER_EMBEDDING_THREADS", "0") or 0)
            if threads > 0:
                options.intra_op_num_threads = threads
            self._session = onnxruntime.InferenceSession(
                hf_hub_download(self._repository, self._model_file), options, providers=["CPUExecutionProvider"]
            )
        return self._session

    def embed(self, samples: np.ndarray, sample_rate: int) -> np.ndarray | None:
        """Unit-length embedding of a mono clip, or None when it is too short."""

        import torch
        import torchaudio

        if sample_rate != SAMPLE_RATE:
            samples = torchaudio.functional.resample(
                torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32)), sample_rate, SAMPLE_RATE
            ).numpy()
        if len(samples) < MIN_EMBEDDING_SECONDS * SAMPLE_RATE:
            return None
        samples = samples[: int(MAX_EMBEDDING_SECONDS * SAMPLE_RATE)]
        # WeSpeaker features use 16-bit sample scale and per-utterance mean normalisation.
        waveform = torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32))[None] * 32768
        features = torchaudio.compliance.kaldi.fbank(
            waveform,
            num_mel_bins=80,
            frame_length=25,
            frame_shift=10,
            dither=0.0,
            sample_frequency=SAMPLE_RATE,
            window_type="hamming",
        )
        features = features - features.mean(0, keepdim=True)
        with self._lock:
            session = self._load()
            vector = session.run(None, {session.get_inputs()[0].name: features.numpy()[None]})[0][0]
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else None
