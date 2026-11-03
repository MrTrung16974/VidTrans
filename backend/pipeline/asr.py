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
from typing import Any, Callable, Sequence

from domain.languages import language_profile, normalize_language

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, float | None], None]

# Silero VAD can discard whole stretches of dialogue mixed with loud music or sound
# effects, and Whisper can lock into a repetition loop that covers real dialogue.
# Stretches at least this long without usable speech are transcribed again.
VAD_GAP_SECONDS = 4.0
VAD_GAP_PADDING_SECONDS = 0.3
# The recovery pass starts this far before the gap: decoding cold inside loud music
# produces garbage, while a little preceding dialogue anchors the decoder. Every second
# here is decoded again for every gap, so it is kept to a couple of short lines.
RECOVERY_CONTEXT_SECONDS = 4.0
# Gaps closer than this are heard in one clip: the context of the second would
# re-decode the speech between them anyway.
RECOVERY_MERGE_SECONDS = 2 * RECOVERY_CONTEXT_SECONDS
# A gap this quiet (RMS of float PCM, about -50 dBFS) holds no speech worth a decode.
SILENT_GAP_RMS = 0.003
# Whisper's own hallucination test: text decoded from audio it believes is not speech.
GAP_MAX_NO_SPEECH_PROB = 0.6
GAP_MIN_LOGPROB = -1.0
# Repetition loops: the same line three or more times in a row, one short unit repeated
# five or more times inside a line, or a few characters stretched over many seconds.
LOOP_MIN_RUN = 3
LOOP_REPEATED_UNIT = re.compile(r"(.{1,4}?)\1{4,}")
LOOP_MIN_SECONDS = 8.0
LOOP_MIN_CHARS_PER_SECOND = 0.6
# No-VAD passes skip muted music only when that saves real decode time.
MIN_MUTED_SAVING_SECONDS = 5.0
MIN_UNMUTED_PIECE_SECONDS = 0.2
UNMUTED_SEPARATOR_SECONDS = 1.0


def _loop_text(text: str) -> str:
    return "".join(character for character in str(text or "") if character.isalnum()).casefold()


def _merge_spans(spans: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for first, last in sorted(spans):
        if merged and first <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


def _muted_overlap(start: float, end: float, muted: Sequence[tuple[float, float]]) -> float:
    return sum(max(0.0, min(end, last) - max(start, first)) for first, last in _merge_spans(muted))


def _silent(audio: Any, rate: int, start: float, end: float) -> bool:
    import numpy as np

    clip = np.asarray(audio[int(start * rate) : int(end * rate)], dtype=np.float32)
    return clip.size == 0 or float(np.sqrt(np.mean(np.square(clip)))) < SILENT_GAP_RMS


# Stock lines Whisper "hears" in music or silence: outros of the videos it was trained
# on. A strong phrase never occurs in real speech, so it always goes; a weak one is
# dropped only when Whisper itself thinks the audio is probably not speech.
STOCK_HALLUCINATION_STRONG = re.compile(
    r"ghiền\s*mì\s*gõ|la\s*la\s*school|amara\.org|明镜与点点|请不吝点赞|字幕由|"
    r"不吝点赞|字幕组|中文字幕|翻译组|字幕志愿者|优优独播剧场|yoyo\s*television|"
    r"không\s+bỏ\s+lỡ\s+những\s+video\s+hấp\s+dẫn",
    re.IGNORECASE,
)
STOCK_HALLUCINATION_WEAK = re.compile(
    r"subscribe|đăng\s*ký\s*(cho\s*)?kênh|cảm\s*ơn\s*các\s*bạn\s*đã\s*(theo\s*dõi|xem)|"
    r"hẹn\s*gặp\s*lại\s*các\s*bạn|thanks?\s*(you\s*)?for\s*watching|谢谢观看|感谢观看|谢谢大家|订阅|点赞",
    re.IGNORECASE,
)
STOCK_WEAK_MIN_NO_SPEECH_PROB = 0.4
# A short cue that tells Whisper the audio is sung: with it, a song comes out as its
# lyrics instead of a stock outro. Only languages listed here get the sung retry.
LYRICS_PROMPTS = {"vi": "Lời bài hát:", "en": "Lyrics:", "zh": "歌词：", "ja": "歌詞：", "ko": "가사:"}


def is_stock_hallucination(segment: dict[str, Any]) -> bool:
    text = str(segment.get("text") or "")
    if STOCK_HALLUCINATION_STRONG.search(text):
        return True
    return bool(
        STOCK_HALLUCINATION_WEAK.search(text)
        and float(segment.get("no_speech_prob") or 0.0) >= STOCK_WEAK_MIN_NO_SPEECH_PROB
    )


def _spoken_seconds(segments: Sequence[dict[str, Any]]) -> float:
    """Seconds of audio covered by cues that hold text (overlaps counted once)."""
    spans = [(float(s["start"]), float(s["end"])) for s in segments if str(s.get("text") or "").strip()]
    return sum(end - start for start, end in _merge_spans(spans))


def drop_stock_hallucinations(segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    kept = [segment for segment in segments if not is_stock_hallucination(segment)]
    return kept, len(segments) - len(kept)


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


def cuda_device_count() -> int:
    """NVIDIA GPUs CTranslate2 can use; 0 without a GPU or a CUDA build."""
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


# Beam search costs about its width in decode time. On a GPU that is cheap, on a
# small CPU it doubles the slowest stage for a barely visible accuracy gain.
GPU_BEAM_SIZE = 5
CPU_BEAM_SIZE = 2


@dataclass(frozen=True)
class ASRConfig:
    engine: str = "faster"          # "faster" or "openai"
    compute_type: str = "int8"      # faster-whisper only: int8, int8_float16, float16, float32
    beam_size: int = 5
    vad: bool = True
    vad_min_silence_ms: int = 500
    device: str = "cpu"             # faster-whisper only: cpu or cuda

    @classmethod
    def from_env(cls, gpu_count: Callable[[], int] = cuda_device_count) -> "ASRConfig":
        """``auto`` values follow the hardware, so a GPU or more cores need no config change."""
        engine = os.environ.get("VIDTRANS_ASR_ENGINE", "faster").strip().lower()
        if engine not in {"faster", "openai"}:
            raise ValueError("VIDTRANS_ASR_ENGINE must be 'faster' or 'openai'")
        device = os.environ.get("VIDTRANS_ASR_DEVICE", "auto").strip().lower() or "auto"
        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError("VIDTRANS_ASR_DEVICE must be auto, cpu or cuda")
        if device == "auto":
            device = "cuda" if engine == "faster" and gpu_count() > 0 else "cpu"
        raw_beam = os.environ.get("VIDTRANS_ASR_BEAM_SIZE", "auto").strip().lower()
        if raw_beam in {"", "auto"}:
            beam_size = GPU_BEAM_SIZE if device == "cuda" else CPU_BEAM_SIZE
        else:
            beam_size = _env_int("VIDTRANS_ASR_BEAM_SIZE", CPU_BEAM_SIZE)
        if beam_size < 1:
            raise ValueError("VIDTRANS_ASR_BEAM_SIZE must be at least 1")
        compute_type = os.environ.get("VIDTRANS_ASR_COMPUTE_TYPE", "auto").strip().lower() or "auto"
        if compute_type == "auto":
            compute_type = "float16" if device == "cuda" else "int8"
        return cls(
            engine=engine,
            compute_type=compute_type,
            beam_size=beam_size,
            vad=_env_bool("VIDTRANS_ASR_VAD", True),
            device=device,
        )


@dataclass(frozen=True)
class ASRResult:
    segments: list[dict[str, Any]]
    detected_language: str | None = None
    language_probability: float | None = None
    # Lines dropped as repetition loops. A line really said several times looks
    # the same to Whisper; burned-in captions can later vouch for them.
    repeated: list[dict[str, Any]] = field(default_factory=list)


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


# faster-whisper computes the log-mel spectrogram of the whole file in one numpy pass,
# whose complex STFT intermediates cost ~46 MB per minute of audio (~3 GB for an hour),
# which got the worker OOM-killed on long videos. The STFT is done in chunks into one
# float32 power array (~5 MB per minute); the mel projection and the global clamp still
# run once over the whole file, so the features stay bit-identical to the reference.
MEL_CHUNK_FRAMES = 30_000  # 5 minutes at 10 ms per frame


def chunked_log_mel(extractor: Any, waveform: Any, padding: int = 160, chunk_length: int | None = None) -> Any:
    """Bit-exact equivalent of faster_whisper FeatureExtractor.__call__ with bounded peak memory."""
    import numpy as np
    from faster_whisper.feature_extractor import FeatureExtractor

    if chunk_length is not None:
        extractor.n_samples = chunk_length * extractor.sampling_rate
        extractor.nb_max_frames = extractor.n_samples // extractor.hop_length
    waveform = np.asarray(waveform, dtype=np.float32)
    if padding:
        waveform = np.pad(waveform, (0, padding))
    n_fft, hop = extractor.n_fft, extractor.hop_length
    if len(waveform) <= n_fft // 2 or len(waveform) // hop <= MEL_CHUNK_FRAMES:
        return FeatureExtractor.__call__(extractor, waveform, padding=0)
    padded = np.pad(waveform, (n_fft // 2, n_fft // 2), mode="reflect")
    del waveform
    window = np.hanning(n_fft + 1)[:-1].astype("float32")
    # The reference STFT yields 1 + (len - n_fft) // hop frames and drops the last one.
    frame_count = (len(padded) - n_fft) // hop
    magnitudes = np.empty((n_fft // 2 + 1, frame_count), dtype=np.float32)
    for first in range(0, frame_count, MEL_CHUNK_FRAMES):
        count = min(MEL_CHUNK_FRAMES, frame_count - first)
        piece = padded[first * hop : first * hop + (count - 1) * hop + n_fft]
        stft = extractor.stft(piece, n_fft, hop, window=window, center=False, return_complex=True).astype("complex64")
        magnitudes[:, first : first + count] = np.abs(stft) ** 2
        del stft
    del padded
    mel_spec = extractor.mel_filters @ magnitudes
    del magnitudes
    log_spec = np.log10(np.clip(mel_spec, a_min=1e-10, a_max=None))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    return (log_spec + 4.0) / 4.0


def bound_feature_memory(model: Any) -> None:
    """Swap the model's extractor class in place so every feature call is chunked."""
    from faster_whisper.feature_extractor import FeatureExtractor

    class ChunkedFeatureExtractor(FeatureExtractor):
        def __call__(self, waveform, padding=160, chunk_length=None):
            return chunked_log_mel(self, waveform, padding, chunk_length)

    model.feature_extractor.__class__ = ChunkedFeatureExtractor


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

                    model = WhisperModel(
                        model_name, device=self.config.device, compute_type=self.config.compute_type,
                        cpu_threads=self.cpu_threads,
                    )
                    bound_feature_memory(model)
                    self._models[key] = model
                else:
                    import whisper

                    self._models[key] = whisper.load_model(model_name)
            return self._models[key]

    def transcribe(
        self, model_name: str, media_path: Path, *, progress: ProgressCallback | None = None,
        source_language: str = "auto", muted: Sequence[tuple[float, float]] = (),
        no_speech: Sequence[tuple[float, float]] = (),
    ) -> ASRResult:
        """Return raw Whisper-style segment dicts (start/end/text/words/avg_logprob).

        ``muted`` spans (seconds) are silenced first: VAD then skips them, and
        Whisper neither spends time on nor invents lyrics for pure music.
        ``no_speech`` spans (the soundtrack classifier heard no speech) are still
        transcribed, but a gap VAD left there is not transcribed a second time.
        """
        source = normalize_language(source_language, allow_auto=True)
        forced = None if source == "auto" else language_profile(source).whisper_code
        model = self.engine(model_name)
        # Decode once: every retry below re-reads the same audio, and passing the
        # array is exactly what both engines would otherwise do internally.
        audio = self._load_audio(model, media_path)
        if muted and hasattr(audio, "shape"):
            audio = audio.copy()
            rate = self._sampling_rate(model)
            for start, end in muted:
                audio[int(start * rate) : int(end * rate)] = 0
        attempts: list[dict[str, Any]] = [
            # Accurate pass: beam search, VAD, hallucination guard.
            {"language": forced, "beam": True, "vad": self.config.vad, "strict": True},
            # VAD can drop speech buried under loud music; retry on the full audio.
            {"language": forced, "beam": False, "vad": False, "strict": False},
            {"language": None, "beam": False, "vad": False, "strict": False},
        ]
        if forced is None:
            # With auto detection the last retry is the same pass again: on a song with no
            # speech that alone cost ~15 minutes of CPU (job 87f54b58).
            attempts.pop()
        for index, attempt in enumerate(attempts):
            if index == 1:
                logger.warning("No transcript segments with requested language, retrying with simpler whisper settings")
            elif index == 2:
                logger.warning("No transcript segments with requested language, retrying with auto language detection")
            if self.config.engine == "faster":
                if attempt["strict"]:
                    result = self._faster_result(model, audio, attempt, progress)
                else:
                    result = self._faster_unmuted(model, audio, attempt, progress, muted)
                if attempt["strict"] and result.segments:
                    result = self._recover_lost_speech(model, audio, result, attempt, [*muted, *no_speech])
            else:
                result = self._openai_result(model, audio, attempt)
            kept, dropped = drop_stock_hallucinations(result.segments)
            if dropped:
                logger.warning("Dropped %d stock Whisper hallucination segment(s)", dropped)
                result = ASRResult(kept, result.detected_language, result.language_probability, result.repeated)
            if dropped:
                # A stock outro where audio plays is almost always a song: Whisper lost the
                # lyrics there. Hear it again as lyrics and keep whichever covers more.
                sung = self._transcribe_as_lyrics(model, audio, forced or result.detected_language, progress, muted)
                if sung is not None and _spoken_seconds(sung.segments) > _spoken_seconds(result.segments):
                    return sung
            if any((segment.get("text") or "").strip() for segment in result.segments):
                return result
        return ASRResult([])

    def _transcribe_as_lyrics(
        self, model: Any, audio: Any, language: str | None, progress: ProgressCallback | None,
        muted: Sequence[tuple[float, float]] = (),
    ) -> ASRResult | None:
        prompt = LYRICS_PROMPTS.get(str(language or "").split("-")[0].lower())
        if not prompt or self.config.engine != "faster":
            return None
        logger.info("Stock hallucination found; hearing the audio again as sung lyrics (%s)", language)
        result = self._faster_unmuted(
            model, audio, {"language": language, "beam": True, "vad": False, "strict": False, "prompt": prompt},
            progress, muted,
        )
        prompt_text = _loop_text(prompt)
        kept = [
            segment for segment in drop_stock_hallucinations(result.segments)[0]
            if _loop_text(segment.get("text")) and _loop_text(segment.get("text")) != prompt_text
        ]
        if not kept:
            return None
        return ASRResult(kept, result.detected_language, result.language_probability, result.repeated)

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

    def _sampling_rate(self, model: Any) -> int:
        if self.config.engine == "faster":
            return int(model.feature_extractor.sampling_rate)
        return 16000

    def _recover_lost_speech(
        self, model: Any, audio: Any, result: ASRResult, attempt: dict[str, Any],
        muted: Sequence[tuple[float, float]] = (),
    ) -> ASRResult:
        """Re-transcribe long stretches with no usable speech: dropped by VAD or eaten by a loop."""

        if not hasattr(audio, "shape"):
            return result
        kept = drop_repetition_loops(result.segments)
        kept_ids = {id(segment) for segment in kept}
        result = ASRResult(kept, result.detected_language, result.language_probability,
                           [segment for segment in result.segments if id(segment) not in kept_ids])
        rate = int(model.feature_extractor.sampling_rate)
        total = len(audio) / rate
        boundaries = [0.0, *(edge for segment in result.segments for edge in (segment["start"], segment["end"])), total]
        gaps = [
            (start, end)
            for start, end in zip(boundaries[::2], boundaries[1::2])
            if end - start - _muted_overlap(start, end, muted) >= VAD_GAP_SECONDS
            and not _silent(audio, rate, start, end)
        ]
        if not gaps:
            return result
        # Gaps close together share one clip; each keeps its own bounds for the filter below.
        clips: list[list[tuple[float, float]]] = []
        for gap in gaps:
            if clips and gap[0] - clips[-1][-1][1] < RECOVERY_MERGE_SECONDS:
                clips[-1].append(gap)
            else:
                clips.append([gap])
        logger.info("Hearing %d gap(s) again in %d clip(s), %.0fs of audio",
                    len(gaps), len(clips), sum(end - start for start, end in gaps))
        language = attempt["language"] or result.detected_language
        # Greedy decoding: these clips are mostly non-speech, where a beam buys nothing.
        gap_attempt = {**attempt, "language": language, "vad": False, "beam": False, "recovery": True}
        recovered: list[dict[str, Any]] = []
        for clip_gaps in clips:
            offset = max(0.0, clip_gaps[0][0] - RECOVERY_CONTEXT_SECONDS)
            clip_end = min(total, clip_gaps[-1][1] + VAD_GAP_PADDING_SECONDS)
            clip = audio[int(offset * rate) : int(clip_end * rate)]
            heard = drop_repetition_loops(self._faster_result(model, clip, gap_attempt, None).segments)
            for segment in heard:
                if segment["no_speech_prob"] > GAP_MAX_NO_SPEECH_PROB and segment["avg_logprob"] < GAP_MIN_LOGPROB:
                    continue
                segment["start"] += offset
                segment["end"] += offset
                for word in segment["words"]:
                    word["start"] = None if word["start"] is None else word["start"] + offset
                    word["end"] = None if word["end"] is None else word["end"] + offset
                # The clip also re-hears known cues around the gaps; keep only speech inside one.
                middle = (segment["start"] + segment["end"]) / 2
                if any(gap_start < middle < gap_end for gap_start, gap_end in clip_gaps):
                    recovered.append(segment)
        if not recovered:
            return result
        logger.info("Recovered %d speech segment(s) lost to VAD or repetition loops", len(recovered))
        segments = sorted([*result.segments, *recovered], key=lambda segment: (segment["start"], segment["end"]))
        return ASRResult(segments, result.detected_language, result.language_probability, result.repeated)

    def _faster_unmuted(
        self, model: Any, audio: Any, attempt: dict[str, Any], progress: ProgressCallback | None,
        muted: Sequence[tuple[float, float]],
    ) -> ASRResult:
        """Decode a no-VAD pass over only the audio left after muting, on the original clock.

        Muted spans are zeros. Without VAD Whisper still decodes them, through its whole
        temperature ladder: a 58 s song muted down to 4.5 s took ~15 min on the 4-core VPS.
        Settings are unchanged; the pieces are joined with a short silence between them.
        """
        if not muted or not hasattr(audio, "shape"):
            return self._faster_result(model, audio, attempt, progress)
        import numpy as np

        rate = int(model.feature_extractor.sampling_rate)
        total = len(audio) / rate
        pieces: list[tuple[float, float]] = []
        cursor = 0.0
        for start, end in _merge_spans(muted):
            if start - cursor > 0:
                pieces.append((cursor, min(start, total)))
            cursor = max(cursor, end)
        if total - cursor > 0:
            pieces.append((cursor, total))
        pieces = [(start, end) for start, end in pieces if end - start >= MIN_UNMUTED_PIECE_SECONDS]
        kept = sum(end - start for start, end in pieces)
        if total - kept < MIN_MUTED_SAVING_SECONDS:
            return self._faster_result(model, audio, attempt, progress)
        if not pieces:
            logger.info("Whole audio is muted music; nothing left to hear without VAD")
            return ASRResult([], attempt["language"])
        logger.info("Hearing %.1fs of unmuted audio instead of %.1fs", kept, total)
        separator = np.zeros(int(UNMUTED_SEPARATOR_SECONDS * rate), dtype=audio.dtype)
        chunks: list[Any] = []
        # (start on the compact clock, end there, start on the original clock)
        table: list[tuple[float, float, float]] = []
        position = 0.0
        for start, end in pieces:
            if chunks:
                chunks.append(separator)
                position += len(separator) / rate
            clip = audio[int(start * rate) : int(end * rate)]
            chunks.append(clip)
            table.append((position, position + len(clip) / rate, start))
            position += len(clip) / rate

        def original(at: float | None) -> float | None:
            if at is None:
                return None
            for begin, finish, origin in reversed(table):
                if at >= begin:
                    return origin + min(at, finish) - begin
            return table[0][2]

        mapped = None
        if progress:
            mapped = lambda at, _total: progress(original(at), total)
        result = self._faster_result(model, np.concatenate(chunks), attempt, mapped)
        for segment in result.segments:
            segment["start"] = original(segment["start"])
            segment["end"] = original(segment["end"])
            for word in segment["words"]:
                word["start"] = original(word["start"])
                word["end"] = original(word["end"])
        return result

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
        if attempt.get("prompt"):
            options.update(temperature=0, condition_on_previous_text=False, initial_prompt=attempt["prompt"])
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
