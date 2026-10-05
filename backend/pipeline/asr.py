"""Chinese speech recognition with faster-whisper, falling back to openai-whisper.

faster-whisper (CTranslate2, int8) is several times faster than openai-whisper on
CPU at comparable accuracy, and its Silero VAD skips music/silence that would
otherwise cost decode time and produce hallucinated cues.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, float | None], None]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class ASRConfig:
    engine: str = "faster"          # "faster" or "openai"
    compute_type: str = "int8"      # faster-whisper only: int8, int8_float32, float32
    beam_size: int = 5
    vad: bool = True
    vad_min_silence_ms: int = 500

    @classmethod
    def from_env(cls) -> "ASRConfig":
        engine = os.environ.get("VIDTRANS_ASR_ENGINE", "faster").strip().lower()
        if engine not in {"faster", "openai"}:
            raise ValueError("VIDTRANS_ASR_ENGINE must be 'faster' or 'openai'")
        beam_size = _env_int("VIDTRANS_ASR_BEAM_SIZE", 5)
        if beam_size < 1:
            raise ValueError("VIDTRANS_ASR_BEAM_SIZE must be at least 1")
        return cls(
            engine=engine,
            compute_type=os.environ.get("VIDTRANS_ASR_COMPUTE_TYPE", "int8").strip() or "int8",
            beam_size=beam_size,
            vad=_env_bool("VIDTRANS_ASR_VAD", True),
        )


def faster_whisper_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


@dataclass
class ASRService:
    config: ASRConfig = field(default_factory=ASRConfig)
    cpu_threads: int = 0  # 0 lets the runtime use every core
    _models: dict[tuple[str, str], Any] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.config.engine == "faster" and not faster_whisper_available():
            logger.warning("faster-whisper is not installed; using openai-whisper")
            self.config = ASRConfig(engine="openai", beam_size=self.config.beam_size)

    @property
    def engine_name(self) -> str:
        return self.config.engine

    def engine(self, model_name: str) -> Any:
        key = (self.config.engine, model_name)
        with self._lock:
            if key not in self._models:
                logger.info("Loading %s-whisper model: %s", self.config.engine, model_name)
                if self.config.engine == "faster":
                    from faster_whisper import WhisperModel

                    self._models[key] = WhisperModel(
                        model_name, device="cpu", compute_type=self.config.compute_type,
                        cpu_threads=self.cpu_threads,
                    )
                else:
                    import whisper

                    self._models[key] = whisper.load_model(model_name)
            return self._models[key]

    def transcribe_chinese(
        self, model_name: str, media_path: Path, *, progress: ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        """Return raw Whisper-style segment dicts (start/end/text/words/avg_logprob)."""
        model = self.engine(model_name)
        attempts: list[dict[str, Any]] = [
            # Accurate pass: beam search, VAD, hallucination guard.
            {"language": "zh", "beam": True, "vad": self.config.vad, "strict": True},
            # VAD can drop speech buried under loud music; retry on the full audio.
            {"language": "zh", "beam": False, "vad": False, "strict": False},
            {"language": None, "beam": False, "vad": False, "strict": False},
        ]
        for index, attempt in enumerate(attempts):
            if index == 1:
                logger.warning("No transcript segments with forced zh, retrying with simpler whisper settings")
            elif index == 2:
                logger.warning("No transcript segments with forced zh, retrying with auto language detection")
            if self.config.engine == "faster":
                segments = self._faster(model, media_path, attempt, progress)
            else:
                segments = self._openai(model, media_path, attempt)
            if any((segment.get("text") or "").strip() for segment in segments):
                return segments
        return []

    def _faster(self, model: Any, media_path: Path, attempt: dict[str, Any],
                progress: ProgressCallback | None) -> list[dict[str, Any]]:
        options: dict[str, Any] = {
            "language": attempt["language"],
            "task": "transcribe",
            "word_timestamps": True,
            "beam_size": self.config.beam_size if attempt["beam"] else 1,
            "vad_filter": attempt["vad"],
        }
        if attempt["vad"]:
            options["vad_parameters"] = {"min_silence_duration_ms": self.config.vad_min_silence_ms}
        if attempt["strict"]:
            options.update(temperature=0, condition_on_previous_text=True, hallucination_silence_threshold=1.0)
        generator, info = model.transcribe(str(media_path), **options)
        total = float(getattr(info, "duration", 0) or 0) or None
        segments = []
        # Decoding happens lazily while iterating, which lets us report real progress.
        for segment in generator:
            segments.append({
                "start": float(segment.start),
                "end": float(segment.end),
                "text": segment.text,
                "avg_logprob": float(segment.avg_logprob),
                "no_speech_prob": float(segment.no_speech_prob),
                "words": [
                    {"word": word.word, "start": word.start, "end": word.end, "probability": word.probability}
                    for word in (segment.words or [])
                ],
            })
            if progress:
                progress(float(segment.end), total)
        return segments

    def _openai(self, model: Any, media_path: Path, attempt: dict[str, Any]) -> list[dict[str, Any]]:
        options: dict[str, Any] = {"task": "transcribe", "fp16": False, "word_timestamps": True, "verbose": False}
        if attempt["language"]:
            options["language"] = attempt["language"]
        if attempt["strict"]:
            options.update(temperature=0, best_of=5, beam_size=self.config.beam_size,
                           condition_on_previous_text=True, hallucination_silence_threshold=1.0)
        return list(model.transcribe(str(media_path), **options).get("segments", []))
