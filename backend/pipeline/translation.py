from __future__ import annotations

import logging
import os
import re
import time
import concurrent.futures
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

# Chars at which a single cue is split before translation.
_SPLIT_CHAR_THRESHOLD: int = int(os.environ.get("VIDTRANS_SPLIT_CHARS", "80"))

# Sentence-boundary punctuation used for splitting long cues.
_SPLIT_RE = re.compile(r"(?<=[。！？；，、…])")


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

def _translate_with_retry(
    text: str,
    translator: TextTranslator,
    *,
    retries: int,
    sleeper: Callable[[float], None],
) -> str:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            translated = _clean(translator.translate(text) or "")
            if translated:
                return translated
            raise RuntimeError("Dịch vụ trả về bản dịch rỗng")
        except Exception as exc:  # pragma: no cover - concrete network errors vary
            last_error = exc
            if attempt + 1 < retries:
                sleeper(min(0.75 * (2**attempt), 4.0))
    raise RuntimeError(f"Không thể dịch sau {retries} lần thử: {last_error}") from last_error


def _translate_with_context_force(
    text: str,
    translator: TextTranslator,
    *,
    retries: int,
    sleeper: Callable[[float], None],
) -> str:
    """Retry translation by prepending a Vietnamese instruction prefix.

    When a translator echoes back Chinese characters, adding an explicit
    instruction often forces it to produce a Vietnamese output.
    """
    prefix = "请翻译成越南语："
    raw = _translate_with_retry(
        f"{prefix}{text}",
        translator,
        retries=retries,
        sleeper=sleeper,
    )
    # Strip the prefix if it leaked into the output (unlikely but defensive)
    cleaned = raw.removeprefix(prefix).strip()
    return cleaned or raw


def _split_long_text(text: str) -> list[str]:
    """Split a long Chinese cue at sentence boundaries for better translation."""
    if len(text) <= _SPLIT_CHAR_THRESHOLD:
        return [text]
    parts = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]
    return parts if len(parts) > 1 else [text]


def _translate_long_cue(
    text: str,
    translator: TextTranslator,
    *,
    retries: int,
    sleeper: Callable[[float], None],
) -> tuple[str, str]:
    """Translate a potentially long cue, splitting if needed.

    Returns ``(translated_text, status)`` where status is one of
    ``"translated"`` or ``"split_translated"``.
    """
    parts = _split_long_text(text)
    if len(parts) == 1:
        result = _translate_with_retry(text, translator, retries=retries, sleeper=sleeper)
        return result, "translated"

    translated_parts: list[str] = []
    for part in parts:
        try:
            translated_parts.append(
                _translate_with_retry(part, translator, retries=retries, sleeper=sleeper)
            )
        except Exception:
            translated_parts.append(part)  # keep original for this fragment
    return " ".join(translated_parts), "split_translated"


def _batch_indexes(source_texts: Sequence[str], max_batch_chars: int) -> list[list[int]]:
    batches: list[list[int]] = []
    current: list[int] = []
    current_size = 0
    for index, text in enumerate(source_texts):
        item_size = len(text) + 24
        if current and current_size + item_size > max_batch_chars:
            batches.append(current)
            current = []
            current_size = 0
        current.append(index)
        current_size += item_size
    if current:
        batches.append(current)
    return batches


def _translate_marked_batch(
    indexes: Sequence[int],
    source_texts: Sequence[str],
    translator: TextTranslator,
    *,
    retries: int,
    sleeper: Callable[[float], None],
) -> dict[int, str]:
    payload = "\n".join(f'<vts id="{index:06d}"/>\n{source_texts[index]}' for index in indexes)
    translated = _translate_with_retry(payload, translator, retries=retries, sleeper=sleeper)
    markers = list(_MARKER_RE.finditer(translated))
    parsed: dict[int, str] = {}
    for position, marker in enumerate(markers):
        index = int(marker.group(1))
        start = marker.end()
        end = markers[position + 1].start() if position + 1 < len(markers) else len(translated)
        value = _clean(translated[start:end])
        if value:
            parsed[index] = value
    if set(parsed) != set(indexes):
        raise RuntimeError("Dịch vụ không giữ đủ marker khi dịch theo batch")
    return parsed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def translate_segments(
    segments: Sequence[dict[str, Any]],
    translator: TextTranslator | None = None,
    *,
    max_batch_chars: int = 1200,
    retries: int = 4,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Translate cues with few network calls and per-cue fallback metadata.

    Markers let a large request keep the original timing boundaries. If a
    provider modifies those markers, only that batch falls back to individual
    translation instead of silently writing every source cue into the output.

    Segments marked with ``skip_subtitle=True`` (by
    :func:`filter_meaningful_segments`) are passed through untouched — their
    source text is kept as-is and they are never sent to the translation
    service.
    """

    if translator is None:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source="zh-CN", target="vi")

    if max_batch_chars < 200:
        raise ValueError("max_batch_chars must be at least 200")
    if retries < 1:
        raise ValueError("retries must be at least 1")

    # Pass-through skipped segments; only work on meaningful ones.
    usable = [
        segment
        for segment in segments
        if not segment.get("skip_subtitle") and _clean(str(segment.get("text") or ""))
    ]
    source_texts = [_clean(str(segment["text"])) for segment in usable]
    translated_by_index: dict[int, str] = {}
    translation_status_by_index: dict[int, str] = {}
    fallback_indexes: set[int] = set()

    batches = _batch_indexes(source_texts, max_batch_chars)

    def process_batch(indexes: list[int]) -> tuple[list[int], dict[int, str]]:
        try:
            res = _translate_marked_batch(
                indexes,
                source_texts,
                translator,
                retries=retries,
                sleeper=sleeper,
            )
            return (indexes, res)
        except Exception as exc:
            logger.warning("Batch translation failed; retrying %d cues individually: %s", len(indexes), exc)
            return (indexes, {})

    def process_individual(index: int) -> tuple[int, str, str, bool]:
        try:
            res, status = _translate_long_cue(
                source_texts[index],
                translator,
                retries=retries,
                sleeper=sleeper,
            )
            return (index, res, status, False)
        except Exception as exc:
            logger.warning("Translation failed for cue %d: %s", index, exc)
            return (index, source_texts[index], "source_fallback", True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # 1. Process batches concurrently
        batch_futures = [executor.submit(process_batch, indexes) for indexes in batches]
        failed_indexes: list[int] = []
        for future in concurrent.futures.as_completed(batch_futures):
            indexes, result = future.result()
            if result:
                translated_by_index.update(result)
                for idx in indexes:
                    translation_status_by_index[idx] = "translated"
            else:
                failed_indexes.extend(indexes)

        # 2. Process failed batch cues individually (with long-cue splitting)
        if failed_indexes:
            indiv_futures = [executor.submit(process_individual, idx) for idx in failed_indexes]
            for future in concurrent.futures.as_completed(indiv_futures):
                idx, res, status, failed = future.result()
                translated_by_index[idx] = res
                translation_status_by_index[idx] = status
                if failed:
                    fallback_indexes.add(idx)

        # 3. Check for unchanged Han and retry with context forcing
        han_check_indexes: list[int] = []
        for index, source_text in enumerate(source_texts):
            translated = _clean(translated_by_index.get(index, ""))
            if contains_han(source_text) and contains_han(translated):
                han_check_indexes.append(index)

        if han_check_indexes:
            def retry_han(index: int) -> tuple[int, str, str, bool]:
                source = source_texts[index]
                # First: plain retry in case it was transient
                try:
                    retried = _translate_with_retry(
                        source,
                        translator,
                        retries=retries,
                        sleeper=sleeper,
                    )
                    if not contains_han(retried):
                        return (index, retried, "translated", False)
                except Exception:
                    pass

                # Second: context-forced translation ("请翻译成越南语：…")
                try:
                    forced = _translate_with_context_force(
                        source,
                        translator,
                        retries=retries,
                        sleeper=sleeper,
                    )
                    if not contains_han(forced):
                        logger.info("Context-force translation succeeded for cue %d", index)
                        return (index, forced, "context_forced", False)
                except Exception:
                    pass

                # Third: split long text and translate each part
                parts = _split_long_text(source)
                if len(parts) > 1:
                    try:
                        translated_parts: list[str] = []
                        for part in parts:
                            t = _translate_with_retry(part, translator, retries=retries, sleeper=sleeper)
                            translated_parts.append(t)
                        joined = " ".join(translated_parts)
                        if not contains_han(joined):
                            logger.info("Split translation succeeded for cue %d (%d parts)", index, len(parts))
                            return (index, joined, "split_translated", False)
                    except Exception:
                        pass

                logger.warning("All translation strategies failed for cue %d; keeping Chinese source", index)
                return (index, source, "source_fallback", True)

            retry_futures = [executor.submit(retry_han, idx) for idx in han_check_indexes]
            for future in concurrent.futures.as_completed(retry_futures):
                idx, res, status, failed = future.result()
                translated_by_index[idx] = res
                translation_status_by_index[idx] = status
                if failed:
                    fallback_indexes.add(idx)
                else:
                    fallback_indexes.discard(idx)

    # Build output — include both skipped and translated segments in original order
    usable_iter = iter(range(len(usable)))
    usable_index_map = {id(seg): i for i, seg in enumerate(usable)}

    # Map by original segment identity for O(1) lookup
    translated_results: dict[int, tuple[str, str, bool]] = {}
    for i, segment in enumerate(usable):
        source_text = source_texts[i]
        translated = _clean(translated_by_index.get(i, "")) or source_text
        unchanged_han = contains_han(source_text) and contains_han(translated)
        used_fallback = i in fallback_indexes or unchanged_han
        status = translation_status_by_index.get(i, "source_fallback" if used_fallback else "translated")
        if unchanged_han and status not in {"source_fallback"}:
            status = "source_fallback"
            used_fallback = True
        translated_results[id(segment)] = (translated, status, used_fallback)

    output: list[dict[str, Any]] = []
    for segment in segments:
        if segment.get("skip_subtitle"):
            # Non-meaningful: pass through with original text, no translation
            source_text = _clean(str(segment.get("text") or ""))
            output.append(
                {
                    **segment,
                    "start": float(segment["start"]),
                    "end": float(segment["end"]),
                    "source_text": source_text,
                    "text": source_text,
                    "translation_status": "skipped",
                    "needs_review": False,
                }
            )
        else:
            seg_id = id(segment)
            if seg_id in translated_results:
                translated, status, used_fallback = translated_results[seg_id]
            else:
                # Segment had empty text — keep as-is
                source_text = _clean(str(segment.get("text") or ""))
                translated, status, used_fallback = source_text, "source_fallback", True
            output.append(
                {
                    **segment,
                    "start": float(segment["start"]),
                    "end": float(segment["end"]),
                    "source_text": _clean(str(segment.get("text") or "")),
                    "text": translated,
                    "translation_status": status,
                    "needs_review": bool(segment.get("needs_review")) or used_fallback,
                }
            )
    return output
