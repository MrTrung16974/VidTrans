"""Multilingual speech recognition with faster-whisper, falling back to openai-whisper.

faster-whisper (CTranslate2, int8) is several times faster than openai-whisper on
CPU at comparable accuracy, and its Silero VAD skips music/silence that would
otherwise cost decode time and produce hallucinated cues.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from domain.languages import language_profile, normalize_language

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, float | None], None]

# Silero VAD can discard whole stretches of dialogue mixed with loud music or sound
# effects, and Whisper can lock into a repetition loop that covers real dialogue.
# Stretches at least this long without usable speech are transcribed again.
VAD_GAP_SECONDS = 4.0
VAD_GAP_PADDING_SECONDS = 0.3
# The recovery pass starts this far before the gap: decoding cold inside loud music
# produces garbage, while a little preceding dialogue anchors the decoder.
RECOVERY_CONTEXT_SECONDS = 10.0
# Whisper's own hallucination test: text decoded from audio it believes is not speech.
GAP_MAX_NO_SPEECH_PROB = 0.6
GAP_MIN_LOGPROB = -1.0
# Repetition loops: the same line three or more times in a row, one short unit repeated
# five or more times inside a line, or a few characters stretched over many seconds.
LOOP_MIN_RUN = 3
LOOP_REPEATED_UNIT = re.compile(r"(.{1,4}?)\1{4,}")
LOOP_MIN_SECONDS = 8.0
LOOP_MIN_CHARS_PER_SECOND = 0.6


def _loop_text(text: str) -> str:
    return "".join(character for character in str(text or "") if character.isalnum()).casefold()


def drop_repetition_loops(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove Whisper repetition-loop output so the time it covered can be re-heard.

    A genuine repeat ("别别别别", "有鬼 有鬼") is short and appears at most twice in a
    row; a loop repeats a whole line, stutters one unit, or stretches over silence.
    """

    kept: list[dict[str, Any]] = []
    run_text, run_length = "", 0
    for segment in segments:
        text = _loop_text(segment.get("text"))
        duration = float(segment["end"]) - float(segment["start"])
        run_length = run_length + 1 if text and text == run_text else 1
        run_text = text
        if run_length >= LOOP_MIN_RUN:
            # The first two of a run may be real; everything after is the loop.
            continue
        if LOOP_REPEATED_UNIT.search(text):
            continue
        if duration >= LOOP_MIN_SECONDS and len(text) / duration < LOOP_MIN_CHARS_PER_SECOND:
            continue
        kept.append(segment)
    if len(kept) != len(segments):
        logger.info("Dropped %d repetition-loop segment(s)", len(segments) - len(kept))
    return kept


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


@dataclass(frozen=True)
class ASRResult:
    segments: list[dict[str, Any]]
    detected_language: str | None = None
    language_probability: float | None = None


def faster_whisper_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def available_cpu_count() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:  # macOS has no CPU affinity API
        return max(1, os.cpu_count() or 1)


def resolve_cpu_threads(configured: int, concurrency: int) -> int:
    """Split the cores between concurrent ASR jobs when no thread count is configured.

    CTranslate2 treats 0 as its own default of 4 threads, which leaves cores idle
    on larger hosts and oversubscribes small ones once two jobs transcribe at once.
    """
    if configured < 0:
        raise ValueError("VIDTRANS_ASR_CPU_THREADS must be 0 (auto) or a positive integer")
    if configured:
        return configured
    return max(1, available_cpu_count() // max(1, concurrency))


@dataclass
class ASRService:
    config: ASRConfig = field(default_factory=ASRConfig)
    cpu_threads: int = 0  # 0 keeps the CTranslate2 default; see resolve_cpu_threads
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

    def transcribe(
        self, model_name: str, media_path: Path, *, progress: ProgressCallback | None = None,
        source_language: str = "auto",
    ) -> ASRResult:
        """Return raw Whisper-style segment dicts (start/end/text/words/avg_logprob)."""
        source = normalize_language(source_language, allow_auto=True)
        forced = None if source == "auto" else language_profile(source).whisper_code
        model = self.engine(model_name)
        # Decode once: every retry below re-reads the same audio, and passing the
        # array is exactly what both engines would otherwise do internally.
        audio = self._load_audio(model, media_path)
        attempts: list[dict[str, Any]] = [
            # Accurate pass: beam search, VAD, hallucination guard.
            {"language": forced, "beam": True, "vad": self.config.vad, "strict": True},
            # VAD can drop speech buried under loud music; retry on the full audio.
            {"language": forced, "beam": False, "vad": False, "strict": False},
            {"language": None, "beam": False, "vad": False, "strict": False},
        ]
        for index, attempt in enumerate(attempts):
            if index == 1:
                logger.warning("No transcript segments with requested language, retrying with simpler whisper settings")
            elif index == 2:
                logger.warning("No transcript segments with requested language, retrying with auto language detection")
            if self.config.engine == "faster":
                result = self._faster_result(model, audio, attempt, progress)
                if attempt["strict"] and result.segments:
                    result = self._recover_lost_speech(model, audio, result, attempt)
            else:
                result = self._openai_result(model, audio, attempt)
            if any((segment.get("text") or "").strip() for segment in result.segments):
                return result
        return ASRResult([])

    def transcribe_chinese(
        self, model_name: str, media_path: Path, *, progress: ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        """Backward-compatible wrapper for legacy callers."""
        return self.transcribe(
            model_name, media_path, progress=progress, source_language="zh-CN"
        ).segments

    def _load_audio(self, model: Any, media_path: Path) -> Any:
        if self.config.engine == "faster":
            from faster_whisper.audio import decode_audio

            return decode_audio(str(media_path), sampling_rate=model.feature_extractor.sampling_rate)
        import whisper

        return whisper.load_audio(str(media_path))

    def _recover_lost_speech(self, model: Any, audio: Any, result: ASRResult, attempt: dict[str, Any]) -> ASRResult:
        """Re-transcribe long stretches with no usable speech: dropped by VAD or eaten by a loop."""

        if not hasattr(audio, "shape"):
            return result
        result = ASRResult(drop_repetition_loops(result.segments), result.detected_language,
                           result.language_probability)
        rate = int(model.feature_extractor.sampling_rate)
        total = len(audio) / rate
        boundaries = [0.0, *(edge for segment in result.segments for edge in (segment["start"], segment["end"])), total]
        gaps = [
            (start, end)
            for start, end in zip(boundaries[::2], boundaries[1::2])
            if end - start >= VAD_GAP_SECONDS
        ]
        if not gaps:
            return result
        language = attempt["language"] or result.detected_language
        recovered: list[dict[str, Any]] = []
        for gap_start, gap_end in gaps:
            offset = max(0.0, gap_start - RECOVERY_CONTEXT_SECONDS)
            clip = audio[int(offset * rate) : int(min(total, gap_end + VAD_GAP_PADDING_SECONDS) * rate)]
            gap_attempt = {**attempt, "language": language, "vad": False, "recovery": True}
            heard = drop_repetition_loops(self._faster_result(model, clip, gap_attempt, None).segments)
            for segment in heard:
                if segment["no_speech_prob"] > GAP_MAX_NO_SPEECH_PROB and segment["avg_logprob"] < GAP_MIN_LOGPROB:
                    continue
                segment["start"] += offset
                segment["end"] += offset
                for word in segment["words"]:
                    word["start"] = None if word["start"] is None else word["start"] + offset
                    word["end"] = None if word["end"] is None else word["end"] + offset
                # The context before the gap re-hears known cues; keep only speech inside the gap.
                if gap_start < (segment["start"] + segment["end"]) / 2 < gap_end:
                    recovered.append(segment)
        if not recovered:
            return result
        logger.info("Recovered %d speech segment(s) lost to VAD or repetition loops", len(recovered))
        segments = sorted([*result.segments, *recovered], key=lambda segment: (segment["start"], segment["end"]))
        return ASRResult(segments, result.detected_language, result.language_probability)

    def _faster(self, model: Any, media_path: Path, attempt: dict[str, Any],
                progress: ProgressCallback | None) -> list[dict[str, Any]]:
        return self._faster_result(model, self._load_audio(model, media_path), attempt, progress).segments

    def _faster_result(self, model: Any, audio: Any, attempt: dict[str, Any],
                       progress: ProgressCallback | None) -> ASRResult:
        options: dict[str, Any] = {
            "language": attempt["language"],
            "task": "transcribe",
            "word_timestamps": True,
            "beam_size": self.config.beam_size if attempt["beam"] else 1,
            "vad_filter": attempt["vad"],
        }
        if attempt["vad"]:
            options["vad_parameters"] = {"min_silence_duration_ms": self.config.vad_min_silence_ms}
        if attempt.get("recovery"):
            # Without the previous-text prompt a loop cannot carry across windows; the silence
            # guard is left out because it discards whole windows of speech under music.
            options.update(temperature=0, condition_on_previous_text=False)
        elif attempt["strict"]:
            options.update(temperature=0, condition_on_previous_text=True, hallucination_silence_threshold=1.0)
        generator, info = model.transcribe(audio, **options)
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
        return ASRResult(
            segments,
            detected_language=getattr(info, "language", None) or attempt["language"],
            language_probability=getattr(info, "language_probability", None),
        )

    def _openai(self, model: Any, media_path: Path, attempt: dict[str, Any]) -> list[dict[str, Any]]:
        return self._openai_result(model, self._load_audio(model, media_path), attempt).segments

    def _openai_result(self, model: Any, audio: Any, attempt: dict[str, Any]) -> ASRResult:
        options: dict[str, Any] = {"task": "transcribe", "fp16": False, "word_timestamps": True, "verbose": False}
        if attempt["language"]:
            options["language"] = attempt["language"]
        if attempt["strict"]:
            options.update(temperature=0, best_of=5, beam_size=self.config.beam_size,
                           condition_on_previous_text=True, hallucination_silence_threshold=1.0)
        payload = model.transcribe(audio, **options)
        return ASRResult(
            list(payload.get("segments", [])),
            detected_language=payload.get("language") or attempt["language"],
        )
