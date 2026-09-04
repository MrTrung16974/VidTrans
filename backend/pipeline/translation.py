from __future__ import annotations

import logging
import re
import time
import concurrent.futures
from typing import Any, Callable, Protocol, Sequence


logger = logging.getLogger(__name__)

# Use HTML-style tags. Translators usually preserve these better than brackets.
_MARKER_RE = re.compile(r'<\s*vts\s+id\s*=\s*["\']?(\d{6})["\']?\s*/?>', re.IGNORECASE)
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


class TextTranslator(Protocol):
    def translate(self, text: str) -> str | None: ...


def contains_han(text: str) -> bool:
    return bool(_HAN_RE.search(text or ""))


def _clean(value: str) -> str:
    return " ".join((value or "").split()).strip()


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
    """

    if translator is None:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source="zh-CN", target="vi")
        
    if max_batch_chars < 200:
        raise ValueError("max_batch_chars must be at least 200")
    if retries < 1:
        raise ValueError("retries must be at least 1")

    usable = [segment for segment in segments if _clean(str(segment.get("text") or ""))]
    source_texts = [_clean(str(segment["text"])) for segment in usable]
    translated_by_index: dict[int, str] = {}
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
            
    def process_individual(index: int) -> tuple[int, str, bool]:
        try:
            res = _translate_with_retry(
                source_texts[index],
                translator,
                retries=retries,
                sleeper=sleeper,
            )
            return (index, res, False)
        except Exception as exc:
            logger.warning("Translation failed for cue %d: %s", index, exc)
            return (index, source_texts[index], True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # 1. Process batches concurrently
        batch_futures = [executor.submit(process_batch, indexes) for indexes in batches]
        failed_indexes: list[int] = []
        for future in concurrent.futures.as_completed(batch_futures):
            indexes, result = future.result()
            if result:
                translated_by_index.update(result)
            else:
                failed_indexes.extend(indexes)
                
        # 2. Process failed batch cues individually concurrently
        if failed_indexes:
            indiv_futures = [executor.submit(process_individual, idx) for idx in failed_indexes]
            for future in concurrent.futures.as_completed(indiv_futures):
                idx, res, failed = future.result()
                translated_by_index[idx] = res
                if failed:
                    fallback_indexes.add(idx)

        # 3. Check for unchanged han and retry them concurrently
        han_check_indexes: list[int] = []
        for index, source_text in enumerate(source_texts):
            translated = _clean(translated_by_index.get(index, ""))
            if contains_han(source_text) and contains_han(translated):
                han_check_indexes.append(index)
                
        if han_check_indexes:
            def retry_han(index: int) -> tuple[int, str, bool]:
                try:
                    retried = _translate_with_retry(
                        source_texts[index],
                        translator,
                        retries=retries,
                        sleeper=sleeper,
                    )
                    if contains_han(retried):
                        raise RuntimeError("Dịch vụ trả lại nguyên văn tiếng Trung")
                    return (index, retried, False)
                except Exception as exc:
                    logger.warning("Translation remained Chinese for cue %d: %s", index, exc)
                    return (index, source_texts[index], True)
                    
            retry_futures = [executor.submit(retry_han, idx) for idx in han_check_indexes]
            for future in concurrent.futures.as_completed(retry_futures):
                idx, res, failed = future.result()
                translated_by_index[idx] = res
                if failed:
                    fallback_indexes.add(idx)
                else:
                    fallback_indexes.discard(idx)

    output: list[dict[str, Any]] = []
    for index, segment in enumerate(usable):
        source_text = source_texts[index]
        translated = _clean(translated_by_index.get(index, "")) or source_text
        unchanged_han = contains_han(source_text) and contains_han(translated)
        used_fallback = index in fallback_indexes or unchanged_han
        output.append(
            {
                **segment,
                "start": float(segment["start"]),
                "end": float(segment["end"]),
                "source_text": source_text,
                "text": translated,
                "translation_status": "source_fallback" if used_fallback else "translated",
                "needs_review": bool(segment.get("needs_review")) or used_fallback,
            }
        )
    return output
