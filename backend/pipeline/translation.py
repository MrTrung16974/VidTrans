from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable, Protocol, Sequence

from pipeline.tts_timing import available_voice_window


logger = logging.getLogger(__name__)

# Use HTML-style tags. Translators usually preserve these better than brackets.
_MARKER_RE = re.compile(r'<\s*vts\s+id\s*=\s*["\'`]?(\d{6})["\'`]?\s*/?>', re.IGNORECASE)
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# Letters of each non-Latin source script. A cue without any of them ("OK", "iPhone",
# "100%") already reads the same in the target language, so a translator correctly
# returns it unchanged; treating that as a failure aborted whole jobs.
_SOURCE_SCRIPT_RE = {
    "zh-CN": _HAN_RE,
    "ja": re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"),
    "ko": re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]"),
    "th": re.compile(r"[\u0e00-\u0e7f]"),
}

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

# Attempts (5s, 10s, 20s, 40s backoff) before giving up on an HTTP 429 provider.
_RATE_LIMIT_RETRIES: int = 5

# A single-letter cue that a translator echoes back is a word fragment (one syllable of
# a name), not untranslated dialogue; it is hidden for review instead of failing the job.
_MAX_FRAGMENT_LETTERS: int = 1

# Latin-script sources have no source-only letters to check, so a short cue echoed back
# ("OK", "iPhone", "NASA", "Jakarta") reads the same in the target language and is kept.
# Longer echoes still fail: they mean the provider did not translate.
_MAX_PRESERVED_LATIN_WORDS: int = 3

# The Vietnamese dubbing voices speak ~5.8 syllables per second; the bounded native
# rate boost and atempo stretch that to ~7. A longer line overflows its window and
# pushes every following line late, so it is rewritten shorter, aiming at ~6.
_MAX_DUB_SYLLABLES_PER_SECOND: float = 7.0
_FIT_DUB_SYLLABLES_PER_SECOND: float = 6.0
_CONDENSE_BATCH_LINES: int = 20
_SYLLABLE_RE = re.compile(r"\w+")

class TextTranslator(Protocol):
    def translate(self, text: str) -> str | None: ...


class LineCondenser(Protocol):
    def condense_lines(
        self, sources: list[str], translations: list[str], max_syllables: list[int]
    ) -> list[str]: ...


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def contains_han(text: str) -> bool:
    return bool(_HAN_RE.search(text or ""))


def needs_translation(text: str, source_language: str) -> bool:
    """False when the cue has nothing in the source script to translate."""
    if not any(character.isalpha() for character in text):
        return False
    script = _SOURCE_SCRIPT_RE.get(source_language)
    return script is None or bool(script.search(text))


def _source_letter_count(text: str, source_language: str) -> int:
    """Letters in the source script; stray Latin letters ("A 纪") do not make a fragment a sentence."""
    script = _SOURCE_SCRIPT_RE.get(source_language)
    if script is None:
        return sum(1 for character in text if character.isalpha())
    return len(script.findall(text))


def segment_filter_signature() -> dict[str, float | int]:
    """Return non-sensitive filter inputs that affect transcript checkpoint output."""

    return {
        "policy_version": 2,
        "min_logprob": _MIN_LOGPROB,
        "max_no_speech_prob": _MAX_NO_SPEECH_PROB,
        "min_han_chars": _MIN_HAN_CHARS,
    }


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
    - After removing filler chars there are no Han characters left

    Confidence scores alone never remove readable dialogue. Whisper can assign
    a low score to real speech (music, accents, overlapping speakers), and
    dropping such a segment makes the subtitle disappear completely. Low
    confidence is instead surfaced as ``needs_review`` by
    :func:`filter_meaningful_segments`.
    """
    text = _clean(str(segment.get("text") or ""))
    if not text:
        return False

    # Filler-only check (covers pure interjection strings)
    if _FILLER_ONLY_RE.match(text):
        return False

    # If the segment contains Han text, require at least one non-filler Han char
    if contains_han(text):
        meaningful_han = [ch for ch in text if _HAN_RE.match(ch) and ch not in _FILLER_CHARS]
        if len(meaningful_han) < _MIN_HAN_CHARS:
            return False

    return True


def _low_confidence_reason(segment: dict[str, Any]) -> str | None:
    try:
        if segment.get("avg_logprob") is not None and float(segment["avg_logprob"]) < _MIN_LOGPROB:
            return "low_logprob"
    except (TypeError, ValueError):
        pass
    try:
        if (
            segment.get("no_speech_prob") is not None
            and float(segment["no_speech_prob"]) > _MAX_NO_SPEECH_PROB
        ):
            return "high_no_speech_probability"
    except (TypeError, ValueError):
        pass
    return None


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
            confidence_reason = _low_confidence_reason(segment)
            kept = {**segment, "skip_subtitle": False, "skip_tts": False}
            if confidence_reason:
                kept["needs_review"] = True
                kept["review_reason"] = confidence_reason
                logger.info(
                    "Keeping low-confidence dialogue [%.2f–%.2f] for review: %r (%s)",
                    segment.get("start", 0),
                    segment.get("end", 0),
                    _clean(str(segment.get("text") or ""))[:40],
                    confidence_reason,
                )
            result.append(kept)
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


def ensure_translation_complete(
    segments: Sequence[dict[str, Any]], *, target_language: str = "vi"
) -> None:
    from domain.languages import language_profile

    target_label = language_profile(target_language).label_vi
    active = [s for s in segments if s.get("translation_status") != "skipped" and not s.get("skip_subtitle")]
    if not active:
        raise TranslationIncompleteError("Không có câu thoại đủ rõ để dịch. Kiểm tra nguồn hoặc dùng model nhận diện lớn hơn.")
    failed = [
        s for s in active
        if s.get("translation_status") in {"source_fallback", "failed"}
        or not _clean(str(s.get("text") or ""))
    ]
    if failed:
        positions = ", ".join(f"{float(s['start']):.1f}s" for s in failed[:5])
        reason = next((str(s["translation_error"]) for s in failed if s.get("translation_error")), "")
        if any("http_429" in str(s.get("translation_error") or "") for s in failed):
            hint = ("Google dịch đang giới hạn tần suất (HTTP 429) với IP máy chủ: đợi 10–30 phút rồi thử lại, "
                    "hoặc cấu hình VIDTRANS_TRANSLATION_PROXY (proxy HTTP/HTTPS) để đổi IP.")
        elif reason == "unchanged_translation":
            hint = ("Dịch vụ dịch trả về nguyên văn câu nguồn. Hãy thử lại hoặc đổi nhà cung cấp dịch; "
                    "chi tiết có trong file bản dịch.")
        else:
            hint = "Kiểm tra kết nối dịch vụ dịch và thử lại; chi tiết có trong file bản dịch."
        if reason:
            hint += f" Lỗi: {reason}."
        raise TranslationIncompleteError(
            f"Chưa dịch được {len(failed)}/{len(active)} câu sang {target_label} (tại {positions}). "
            "Đã dừng trước khi lồng tiếng/xuất video để tránh chèn lại nội dung nguồn. " + hint
        )


def repair_leaked_translations(
    segments: list[dict[str, Any]],
    translator: TextTranslator,
    *,
    source_language: str,
) -> int:
    """Retranslate cues whose translation still carries source-script words.

    Older cached translations kept Chinese names ("雅荣 cũng nói..."); subtitles
    showed them and the dubbing voice skipped them. A cue is replaced only when
    the new translation is free of source script. Returns the repaired count.
    """

    script = _SOURCE_SCRIPT_RE.get(source_language)
    if script is None:
        return 0
    repaired = 0
    for segment in segments:
        text = str(segment.get("text") or "")
        source = str(segment.get("source_text") or "")
        if segment.get("skip_subtitle") or not script.search(text) or not source or _FILLER_ONLY_RE.match(source):
            continue
        try:
            value = _clean(translator.translate(source))
        except Exception as exc:
            logger.warning("Cannot repair a leaked translation: %s", type(exc).__name__)
            continue
        if value and not script.search(value):
            segment["text"] = value
            segment["translation_repaired"] = True
            repaired += 1
    return repaired


def fit_translations_to_voice(
    segments: list[dict[str, Any]],
    condenser: LineCondenser,
    *,
    check_active: Callable[[], None] = lambda: None,
) -> int:
    """Rewrite dubbing lines too long for their voice window or still in source script.

    Line-by-line translators know nothing of timing; their long lines overflowed by
    a third and delayed the dubbing by seconds. A translator that keeps a name in
    Chinese leaves the cue unvoiced. A rewrite is kept only when it is free of source
    script and, for a line that only ran long, shorter. Returns the rewritten count.
    """

    candidates: list[tuple[int, int, bool]] = []
    for index, segment in enumerate(segments):
        text = str(segment.get("text") or "")
        if segment.get("skip_tts") or segment.get("skip_subtitle") or not text or not segment.get("source_text"):
            continue
        script = _SOURCE_SCRIPT_RE.get(str(segment.get("source_language") or ""))
        leaked = bool(script and script.search(text))
        window = available_voice_window(segments, index)
        if leaked or len(_SYLLABLE_RE.findall(text)) > window * _MAX_DUB_SYLLABLES_PER_SECOND:
            candidates.append((index, max(2, int(window * _FIT_DUB_SYLLABLES_PER_SECOND)), leaked))

    rewritten = 0
    for offset in range(0, len(candidates), _CONDENSE_BATCH_LINES):
        check_active()
        batch = candidates[offset : offset + _CONDENSE_BATCH_LINES]
        try:
            values = condenser.condense_lines(
                [str(segments[index]["source_text"]) for index, _, _ in batch],
                [str(segments[index]["text"]) for index, _, _ in batch],
                [limit for _, limit, _ in batch],
            )
        except Exception as exc:
            logger.warning("Cannot shorten %d dubbing lines: %s", len(batch), type(exc).__name__)
            continue
        for (index, _, leaked), value in zip(batch, values):
            segment = segments[index]
            value = _clean(value)
            script = _SOURCE_SCRIPT_RE.get(str(segment.get("source_language") or ""))
            if not value or (script and script.search(value)):
                continue
            if not leaked and len(_SYLLABLE_RE.findall(value)) >= len(_SYLLABLE_RE.findall(str(segment["text"]))):
                continue
            segment["text_before_fit"] = segment["text"]
            segment["text"] = value
            segment["translation_condensed"] = True
            rewritten += 1
    return rewritten


def _translate_with_retry(
    text: str,
    translator: TextTranslator,
    *,
    retries: int,
    sleeper: Callable[[float], None],
    check_active: Callable[[], None],
) -> str:
    def call() -> str:
        translated = _clean(translator.translate(text) or "")
        if not translated:
            raise ValueError("empty_translation")
        if translated.casefold() == _clean(text).casefold():
            raise ValueError("unchanged_translation")
        return translated

    return _call_with_retry(call, retries=retries, sleeper=sleeper, check_active=check_active)


def _call_with_retry(
    call: Callable[[], Any],
    *,
    retries: int,
    sleeper: Callable[[float], None],
    check_active: Callable[[], None],
) -> Any:
    last_error: Exception | None = None
    attempt = 0
    for attempt in range(max(retries, _RATE_LIMIT_RETRIES)):
        check_active()
        try:
            return call()
        except Exception as exc:
            last_error = exc
        check_active()
        if getattr(last_error, "rate_limited", False):
            # Short retries only extend an HTTP 429 block; wait meaningfully.
            attempts = max(retries, _RATE_LIMIT_RETRIES)
        else:
            attempts = retries
        if attempt + 1 < attempts:
            sleeper(_retry_delay(last_error, attempt))
        else:
            break
    raise RuntimeError(f"Dịch thất bại sau {attempt + 1} lần: {type(last_error).__name__}") from last_error


def _provider_blocked(error: BaseException) -> bool:
    """Rate limits, bad configuration and unreachable providers affect every cue alike."""
    return any(getattr(error, flag, False) for flag in ("rate_limited", "permanent", "unavailable"))


def _safe_reason(error: BaseException) -> str:
    from infrastructure.translator import TranslationServiceError
    # Provider errors are sanitised; other exceptions may echo source text.
    return str(error)[:160] if isinstance(error, TranslationServiceError) else ""


def _retry_delay(error: Exception | None, attempt: int) -> float:
    if getattr(error, "rate_limited", False):
        retry_after = getattr(error, "retry_after", None) or 0.0
        return min(max(float(retry_after), 5.0 * (2**attempt)), 60.0)
    return min(0.75 * (2**attempt), 4.0)


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
        if not value:
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
    source_language: str = "zh-CN",
    target_language: str = "vi",
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
        from infrastructure.translator import MultilingualTranslator
        translator = MultilingualTranslator(
            source_language=source_language, target_language=target_language
        )

    sources = [_clean(str(s.get("source_text") or s.get("text") or "")) for s in segments]
    active = [i for i, s in enumerate(segments) if not s.get("skip_subtitle") and sources[i]]
    translations: dict[int, tuple[str, str, str | None]] = {}
    cache: dict[str, tuple[str, str, str | None]] = {}
    check_active()
    for index in active:
        if not needs_translation(sources[index], source_language):
            translations[index] = (sources[index], "preserved", None)
    active_to_translate = [index for index in active if index not in translations]

    if getattr(translator, "supports_markers", True):
        texts = [sources[i] for i in active_to_translate]
        for batch in _batch_indexes(texts, max_batch_chars):
            if len(batch) < 2 or any(len(texts[i]) > max_batch_chars for i in batch):
                continue
            payload = "\n".join(f'<vts id="{i:06d}"/>\n{texts[i]}' for i in batch)
            try:
                raw = _translate_with_retry(payload, translator, retries=1,
                                            sleeper=sleeper, check_active=check_active)
                parsed = _parse_marked_batch(raw, batch)
                for index, value in parsed.items():
                    translations[active_to_translate[index]] = (value, "translated", None)
                    cache[texts[index]] = (value, "translated", None)
            except Exception as exc:
                check_active()
                logger.warning("Batch translation failed (%s); retrying complete cues", type(exc).__name__)
    elif hasattr(translator, "translate_lines"):
        # One request per batch instead of per cue keeps long videos under provider rate limits.
        unique = list(dict.fromkeys(sources[i] for i in active_to_translate))
        # LLM translators fit each line to the time the speaker takes to say it.
        seconds: dict[str, float] = {}
        for i in active_to_translate:
            seconds.setdefault(sources[i], max(0.0, float(segments[i]["end"]) - float(segments[i]["start"])))
        accepts_durations = getattr(translator, "accepts_durations", False)
        for batch in _batch_indexes(unique, max_batch_chars):
            if len(batch) < 2 or any(len(unique[i]) > max_batch_chars for i in batch):
                continue
            lines = [unique[i] for i in batch]
            durations = [seconds.get(line) for line in lines]
            try:
                values = _call_with_retry(
                    (lambda: translator.translate_lines(lines, durations=durations))
                    if accepts_durations else (lambda: translator.translate_lines(lines)),
                    retries=1, sleeper=sleeper, check_active=check_active,
                )
                if len(values) != len(lines):
                    raise ValueError("missing_batch_lines")
                for line, value in zip(lines, values):
                    value = _clean(value)
                    if value and value.casefold() != line.casefold():
                        cache[line] = (value, "translated", None)
            except Exception as exc:
                check_active()
                cause = exc.__cause__ or exc
                logger.warning("Batch translation failed (%s: %s); retrying complete cues",
                               type(cause).__name__, _safe_reason(cause))
                if _provider_blocked(cause):
                    break

    rate_limit_reason: str | None = None
    for completed, index in enumerate(active, 1):
        check_active()
        source = sources[index]
        if index not in translations:
            if source not in cache and rate_limit_reason:
                # Provider is still blocking after full backoff; do not hammer it cue by cue.
                cache[source] = (source, "source_fallback", rate_limit_reason)
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
                    from infrastructure.translator import TranslationServiceError
                    reason = str(cause) if isinstance(cause, (ValueError, TranslationServiceError)) else type(cause).__name__
                    fragment = (
                        reason == "unchanged_translation"
                        and _source_letter_count(source, source_language) <= _MAX_FRAGMENT_LETTERS
                    )
                    preserved = (
                        reason == "unchanged_translation"
                        and source_language not in _SOURCE_SCRIPT_RE
                        and len(source.split()) <= _MAX_PRESERVED_LATIN_WORDS
                    )
                    if preserved:
                        cache[source] = (source, "preserved", None)
                    else:
                        cache[source] = (source, "dropped_fragment" if fragment else "source_fallback", reason[:160])
                        if _provider_blocked(cause):
                            rate_limit_reason = reason[:160]
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
        provider_for = getattr(translator, "provider_for", None)
        used_fallback = getattr(translator, "used_fallback_for", None)
        result = {
            **segment, "start": float(segment["start"]), "end": float(segment["end"]),
            "source_text": sources[index], "text": text, "translation_status": status,
            "translation_provider": (
                provider_for(sources[index]) if provider_for and status != "preserved"
                else getattr(translator, "name", type(translator).__name__)
            ),
            # Translated by a backup provider because the chosen one failed on this cue.
            "translation_fallback": bool(used_fallback and used_fallback(sources[index])),
            "source_language": source_language,
            "target_language": target_language,
            "needs_review": bool(segment.get("needs_review")) or status in {"source_fallback", "dropped_fragment"},
        }
        if status == "dropped_fragment":
            # Never show or speak the source-language fragment.
            result.update(skip_subtitle=True, skip_tts=True, review_reason="untranslated_fragment")
        # An imported/retried JSON must not keep the old render_text.
        result.pop("subtitle_layout", None)
        result.pop("translation_error", None)
        if error:
            result["translation_error"] = error
        output.append(result)
    return output


def translate_segments_by_source_language(
    segments: Sequence[dict[str, Any]],
    *,
    target_language: str,
    default_source_language: str,
    translator_factory: Callable[[str, str], TextTranslator] | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Translate cue groups independently, then restore their original order."""

    if translator_factory is None:
        from infrastructure.translator import MultilingualTranslator
        translator_factory = lambda source, target: MultilingualTranslator(
            source_language=source, target_language=target
        )

    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, segment in enumerate(segments):
        source = str(segment.get("source_language") or default_source_language)
        groups.setdefault(source, []).append((index, segment))

    output: list[dict[str, Any] | None] = [None] * len(segments)
    for source_language, indexed_segments in groups.items():
        if source_language == "auto":
            for index, segment in indexed_segments:
                output[index] = {
                    **segment,
                    "source_text": _clean(str(segment.get("text") or "")),
                    "translation_status": "source_fallback",
                    "translation_error": "unknown_source_language",
                    "source_language": source_language,
                    "target_language": target_language,
                    "needs_review": True,
                }
            continue
        if source_language == target_language:
            for index, segment in indexed_segments:
                output[index] = {
                    **segment,
                    "source_text": _clean(str(segment.get("text") or "")),
                    "translation_status": "source_passthrough",
                    "translation_provider": "passthrough",
                    "source_language": source_language,
                    "target_language": target_language,
                }
            continue
        translated = translate_segments(
            [segment for _, segment in indexed_segments],
            translator_factory(source_language, target_language),
            source_language=source_language,
            target_language=target_language,
            **kwargs,
        )
        for (index, _), segment in zip(indexed_segments, translated):
            output[index] = segment

    return [segment for segment in output if segment is not None]
