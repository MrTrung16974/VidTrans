"""Separate the voice from background music with Demucs.

Separation costs about twice the clip length on a 4-core CPU, so callers run
it only when the scene classifier hears speech under music, and it is off by
default (VIDTRANS_VOCAL_SEPARATION). The vocal stem feeds Whisper only: a
background song's singer stays in it, so voice analysis keeps the original.
"""

from __future__ import annotations

import logging
import os
import threading
import wave
from pathlib import Path
from typing import Any

import numpy as np


logger = logging.getLogger(__name__)

DEMUCS_MODEL = "htdemucs"
OUTPUT_SAMPLE_RATE = 16000
# Weights live next to the Hugging Face cache, which Docker keeps in a volume.
TORCH_HUB_DIR = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "torch-hub"


class VocalSeparator:
    def __init__(self, model_name: str = DEMUCS_MODEL) -> None:
        self._model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._model is None:
            import torch
            from demucs.pretrained import get_model

            torch.hub.set_dir(str(TORCH_HUB_DIR))
            model = get_model(self._model_name)
            model.eval()
            self._model = model
        return self._model

    def separate(self, media_path: Path, output_path: Path) -> Path:
        """Write the vocal stem of ``media_path`` as 16 kHz mono PCM WAV."""

        import torch
        import torchaudio
        from demucs.apply import apply_model
        from faster_whisper.audio import decode_audio

        with self._lock:
            model = self._load()
            left, right = decode_audio(str(media_path), sampling_rate=model.samplerate, split_stereo=True)
            mix = torch.from_numpy(np.stack([left, right]).astype(np.float32))
            # Demucs expects roughly unit-variance input; restore the level afterwards.
            reference = mix.mean(0)
            scale = float(reference.std()) or 1.0
            centre = float(reference.mean())
            with torch.inference_mode():
                sources = apply_model(
                    model, ((mix - centre) / scale)[None], shifts=0, overlap=0.1, split=True, progress=False
                )[0]
            vocals = sources[model.sources.index("vocals")].mean(0) * scale + centre
            vocals = torchaudio.functional.resample(vocals, model.samplerate, OUTPUT_SAMPLE_RATE)
        pcm = (np.clip(vocals.numpy(), -1.0, 1.0) * 32767).astype("<i2")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".partial.wav")
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(OUTPUT_SAMPLE_RATE)
            output.writeframes(pcm.tobytes())
        temporary.replace(output_path)
        return output_path
