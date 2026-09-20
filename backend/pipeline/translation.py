from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable, Protocol, Sequence


logger = logging.getLogger(__name__)

# Use HTML-style tags. Translators usually preserve these better than brackets.
_MARKER_RE = re.compile(r'<\s*vts\s+id\s*=\s*["\'`]?(\d{6})["\'`]?\s*/?>', re.IGNORECASE)
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# Filler / interjection characters that carry no translatable meaning.
# These are single-syllable Mandarin fillers, hesitation sounds, and
# discourse markers that Whisper often picks up from pauses or background noise.
_FILLER_CHARS = frozenset(
    "嗯啊呃哦哟唉诶哎哈呵嘿嘻哼喂哇咦噢噗唔嚯哩嘛呀呢吧啦啵呗哟喔嘁"
    "额唔咳哼唷哟嘿哦唉呦噢哇嘿嘻哈哈嘻嘻"
)

# Regex: text that is *only* filler chars + punctuation + whitespace + numbers
_FILLER_ONLY_RE = re.compile(
    r"^[\s\d"
    r"\u3002\uff0c\uff01\uff1f\u3001\uff1b\uff1a\u300c\u300d\u2026\u2014"  # CJK punct
    r".,!?;:\"'\-–—…\(\)\[\]"
    r"\u5450\u554a\u5462\u5a46\u563f\u5440\u554e\u5445\u55ef\u9f3b"       # misc filler han
    + "".join(_FILLER_CHARS)
    + r"]+$",
    re.UNICODE,
)

# Min logprob below which Whisper segments are considered hallucinations.
# Override with env var VIDTRANS_MIN_LOGPROB (float, e.g. "-1.2").
_MIN_LOGPROB: float = float(os.environ.get("VIDTRANS_MIN_LOGPROB", "-1.0"))

# Max no_speech_prob above which a segment is considered silence/noise.
_MAX_NO_SPEECH_PROB: float = float(os.environ.get("VIDTRANS_MAX_NO_SPEECH_PROB", "0.8"))

# Minimum number of distinct Han characters after stripping fillers.
_MIN_HAN_CHARS: int = 1

class TextTranslator(Protocol):
    def translate(self, text: str) -> str | None: ...


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def contains_han(text: str) -> bool:
    return bool(_HAN_RE.search(text or ""))


def _clean(value: str) -> str:
    return " ".join((value or "").split()).strip()


# ---------------------------------------------------------------------------
# Segment filtering
# ---------------------------------------------------------------------------

def _strip_fillers(text: str) -> str:
    """Remove pure filler characters, leaving only substantive Han + others."""
    return "".join(ch for ch in text if ch not in _FILLER_CHARS)


def is_meaningful_segment(segment: dict[str, Any]) -> bool:
    """Return True if the segment contains translatable content worth keeping.

    A segment is *not* meaningful if any of these hold:
    - Text is empty after normalisation
    - Text consists only of filler interjections / punctuation / whitespace
    - Whisper ``avg_logprob`` is below the hallucination threshold
    - Whisper ``no_speech_prob`` is above the silence threshold
    - After removing filler chars there are no Han characters left
    """
    text = _clean(str(segment.get("text") or ""))
    if not text:
        return False

    # Check Whisper confidence scores when available
    avg_logprob = segment.get("avg_logprob")
    if avg_logprob is not None:
        try:
            if float(avg_logprob) < _MIN_LOGPROB:
                return False
        except (TypeError, ValueError):
            pass

    no_speech_prob = segment.get("no_speech_prob")
    if no_speech_prob is not None:
        try:
            if float(no_speech_prob) > _MAX_NO_SPEECH_PROB:
                return False
        except (TypeError, ValueError):
            pass

    # Filler-only check (covers pure interjection strings)
    if _FILLER_ONLY_RE.match(text):
        return False

    # If the segment contains Han text, require at least one non-filler Han char
    if contains_han(text):
        meaningful_han = [ch for ch in text if _HAN_RE.match(ch) and ch not in _FILLER_CHARS]
        if len(meaningful_han) < _MIN_HAN_CHARS:
            return False

    return True


def filter_meaningful_segments(
    segments: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mark non-meaningful segments with ``skip_subtitle`` and ``skip_tts``.

    The original list structure and timing are preserved so that callers that
    need an index-stable view (e.g. SRT writers) can still iterate the full
    list and simply skip flagged entries.  Meaningful segments are returned
    unchanged (flags are absent / False).
    """
    result: list[dict[str, Any]] = []
    skipped = 0
    for segment in segments:
        if is_meaningful_segment(segment):
            result.append({**segment, "skip_subtitle": False, "skip_tts": False})
        else:
            text = _clean(str(segment.get("text") or ""))
            logger.debug(
                "Skipping non-meaningful segment [%.2f–%.2f]: %r (logprob=%s, no_speech=%s)",
                segment.get("start", 0),
                segment.get("end", 0),
                text[:40],
                segment.get("avg_logprob"),
                segment.get("no_speech_prob"),
            )
            skipped += 1
            result.append({**segment, "skip_subtitle": True, "skip_tts": True})
    if skipped:
        logger.info("Filtered %d non-meaningful segment(s) out of %d total", skipped, len(result))
    return result


# ---------------------------------------------------------------------------
# Translation internals
# ---------------------------------------------------------------------------

class TranslationIncompleteError(RuntimeError):
    """Prevent rendering/dubbing source text as a successful translation."""


def ensure_translation_complete(segments: Sequence[dict[str, Any]]) -> None:
    active = [s for s in segments if s.get("translation_status") != "skipped" and not s.get("skip_subtitle")]
    if not active:
        raise TranslationIncompleteError("Không có câu thoại đủ rõ để dịch. Kiểm tra nguồn hoặc dùng model nhận diện lớn hơn.")
    failed = [
        s for s in active
        if s.get("translation_status") in {"source_fallback", "failed"}
        or not _clean(str(s.get("text") or ""))
        or contains_han(str(s.get("text") or ""))
    ]
    if failed:
        positions = ", ".join(f"{float(s['start']):.1f}s" for s in failed[:5])
        raise TranslationIncompleteError(
            f"Chưa dịch được {len(failed)}/{len(active)} câu sang tiếng Việt (tại {positions}). "
            "Đã dừng trước khi lồng tiếng/xuất video để tránh chèn lại chữ Trung. "
            "Kiểm tra kết nối dịch vụ dịch và thử lại; chi tiết có trong file bản dịch."
        )


def _translate_with_retry(
    text: str,
    translator: TextTranslator,
    *,
    retries: int,
    sleeper: Callable[[float], None],
    check_active: Callable[[], None],
) -> str:
    last_error: Exception | None = None
    for attempt in range(retries):
        check_active()
        try:
            translated = _clean(translator.translate(text) or "")
            if not translated:
                raise ValueError("empty_translation")
            if contains_han(translated):
                raise ValueError("untranslated_chinese")
            if translated.casefold() == _clean(text).casefold():
                raise ValueError("unchanged_translation")
            return translated
        except Exception as exc:
            last_error = exc
        check_active()
        if attempt + 1 < retries:
            sleeper(min(0.75 * (2**attempt), 4.0))
    raise RuntimeError(f"Dịch thất bại sau {retries} lần: {type(last_error).__name__}") from last_error


def _split_long_text(text: str, *, limit: int = 1200) -> list[str]:
    """Preserve complete sentences; split only at the provider request limit."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        candidates = [m.end() for m in re.finditer(r"[。！？；，、….!?;\s]", remaining[:limit])]
        boundary = candidates[-1] if candidates and candidates[-1] >= limit // 2 else limit
        parts.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _batch_indexes(source_texts: Sequence[str], max_batch_chars: int) -> list[list[int]]:
    batches: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, text in enumerate(source_texts):
        item_size = len(text) + 24
        if current and size + item_size > max_batch_chars:
            batches.append(current)
            current, size = [], 0
        current.append(index)
        size += item_size
    if current:
        batches.append(current)
    return batches


def _parse_marked_batch(translated: str, indexes: Sequence[int]) -> dict[int, str]:
    markers = list(_MARKER_RE.finditer(translated))
    ids = [int(marker.group(1)) for marker in markers]
    if ids != list(indexes) or (markers and translated[:markers[0].start()].strip()):
        raise ValueError("missing_or_reordered_markers")
    parsed = {}
    for position, marker in enumerate(markers):
        end = markers[position + 1].start() if position + 1 < len(markers) else len(translated)
        value = _clean(translated[marker.end():end])
        if not value or contains_han(value):
            raise ValueError("invalid_batch_translation")
        parsed[int(marker.group(1))] = value
    return parsed


def translate_segments(
    segments: Sequence[dict[str, Any]],
    translator: TextTranslator | None = None,
    *,
    max_batch_chars: int = 1200,
    retries: int = 3,
    sleeper: Callable[[float], None] = time.sleep,
    check_active: Callable[[], None] = lambda: None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Translate complete cues with bounded retries and stable timestamps.

    Calls are serial: HTML translation clients mutate request parameters.
    Repeated cues share a per-job cache. Marker batching is only used for
    providers that support it; failed batches fall back to complete cues.
    Failed source text is retained for diagnostics, never approved for render.
    """
    if max_batch_chars < 200 or retries < 1:
        raise ValueError("max_batch_chars must be >= 200 and retries must be >= 1")
    if translator is None:
        from infrastructure.vietnamese_translator import GoogleVietnameseTranslator
        translator = GoogleVietnameseTranslator()

    sources = [_clean(str(s.get("source_text") or s.get("text") or "")) for s in segments]
    active = [i for i, s in enumerate(segments) if not s.get("skip_subtitle") and sources[i]]
    translations: dict[int, tuple[str, str, str | None]] = {}
    cache: dict[str, tuple[str, str, str | None]] = {}
    check_active()

    if getattr(translator, "supports_markers", True):
        texts = [sources[i] for i in active]
        for batch in _batch_indexes(texts, max_batch_chars):
            if len(batch) < 2 or any(len(texts[i]) > max_batch_chars for i in batch):
                continue
            payload = "\n".join(f'<vts id="{i:06d}"/>\n{texts[i]}' for i in batch)
            try:
                raw = _translate_with_retry(payload, translator, retries=1,
                                            sleeper=sleeper, check_active=check_active)
                parsed = _parse_marked_batch(raw, batch)
                for index, value in parsed.items():
                    translations[active[index]] = (value, "translated", None)
                    cache[texts[index]] = (value, "translated", None)
            except Exception as exc:
                check_active()
                logger.warning("Batch translation failed (%s); retrying complete cues", type(exc).__name__)

    for completed, index in enumerate(active, 1):
        check_active()
        source = sources[index]
        if index not in translations:
            if source not in cache:
                try:
                    parts = _split_long_text(source, limit=max_batch_chars)
                    translated_parts = [
                        _translate_with_retry(part, translator, retries=retries,
                                              sleeper=sleeper, check_active=check_active)
                        for part in parts
                    ]
                    value = " ".join(translated_parts)
                    status = "split_translated" if len(parts) > 1 else "translated"
                    cache[source] = (value, status, None)
                except Exception as exc:
                    check_active()
                    # Retain structured failure, never silently mix translated/source fragments.
                    cause = exc.__cause__ or exc
                    reason = str(cause) if isinstance(cause, ValueError) else type(cause).__name__
                    cache[source] = (source, "source_fallback", reason[:160])
                    logger.warning("Translation failed at %.2fs (%s)", float(segments[index]["start"]), reason[:160])
            translations[index] = cache[source]
        if progress_callback:
            progress_callback(completed, len(active))

    output = []
    for index, segment in enumerate(segments):
        skipped = bool(segment.get("skip_subtitle"))
        text, status, error = translations.get(
            index, (sources[index], "skipped" if skipped else "source_fallback", None if skipped else "empty_source")
        )
        result = {
            **segment, "start": float(segment["start"]), "end": float(segment["end"]),
            "source_text": sources[index], "text": text, "translation_status": status,
            "translation_provider": getattr(translator, "name", type(translator).__name__),
            "target_language": "vi",
            "needs_review": bool(segment.get("needs_review")) or status == "source_fallback",
        }
        # An imported/retried JSON must not keep the old render_text.
        result.pop("subtitle_layout", None)
        result.pop("translation_error", None)
        if error:
            result["translation_error"] = error
        output.append(result)
    return output
