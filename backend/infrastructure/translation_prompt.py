"""Subtitle/dubbing translation prompt shared by the LLM translators (Gemini, Qwen3).

Instructions are in English: small local models follow English instructions
more reliably than Vietnamese ones. Every cue carries its spoken duration so
the model can keep the line short enough for the dubbing voice, and the last
translated cues are passed as read-only context so forms of address stay
consistent across batches.
"""

from __future__ import annotations

import json
import re
from collections import deque
from typing import Any, Sequence

from infrastructure.vietnamese_translator import TranslationServiceError

CJK_IDEOGRAPH = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
CJK_TARGETS = frozenset({"zh-CN", "ja", "ko"})
HISTORY_SIZE = 6

LANGUAGE_NAMES = {
    "vi": "Vietnamese", "en": "English", "zh-CN": "Chinese", "ja": "Japanese",
    "ko": "Korean", "th": "Thai", "id": "Indonesian", "es": "Spanish",
}

# Comfortable dubbing speed, in spoken units per second of source speech.
VIETNAMESE_SYLLABLES_PER_SECOND = 4.0

# Characters that only exist in Traditional Chinese, paired with their
# Simplified forms; enough to tell the two scripts apart in a subtitle line.
_TRADITIONAL = "這個們來時說會對愛為後還過沒樣話點麼現開見覺嗎讓機與從經種實長問無間聽頭錢謝學國歡數東車門裡氣電體發邊動當從於"
_SIMPLIFIED = "这个们来时说会对爱为后还过没样话点么现开见觉吗让机与从经种实长问无间听头钱谢学国欢数东车门里气电体发边动当从于"
_TRADITIONAL_ONLY = frozenset(_TRADITIONAL) - frozenset(_SIMPLIFIED)
_SIMPLIFIED_ONLY = frozenset(_SIMPLIFIED) - frozenset(_TRADITIONAL)


def is_traditional_chinese(text: str) -> bool:
    traditional = sum(1 for char in text if char in _TRADITIONAL_ONLY)
    simplified = sum(1 for char in text if char in _SIMPLIFIED_ONLY)
    return traditional > simplified


def language_name(code: str, sample: str = "") -> str:
    if code == "zh-CN" and sample and is_traditional_chinese(sample):
        return "Traditional Chinese"
    return LANGUAGE_NAMES.get(code, code)


def leaks_source_script(text: str, target_language: str) -> bool:
    """A non-CJK translation must not keep Chinese characters."""

    return target_language not in CJK_TARGETS and bool(CJK_IDEOGRAPH.search(text))


def _length_rule(target_language: str) -> str:
    if target_language == "vi":
        return (
            "Each item has `seconds`, the time the original speaker takes to say it. The translation is spoken "
            f"by a dubbing voice in that time, so keep it to about {VIETNAMESE_SYLLABLES_PER_SECOND:g} Vietnamese "
            "syllables per second. Prefer concise, natural spoken wording over a literal, long sentence."
        )
    return (
        "Each item has `seconds`, the time the original speaker takes to say it. The translation is spoken "
        "by a dubbing voice in that time, so keep it concise and natural to say aloud."
    )


def _address_rule(target_language: str) -> str:
    if target_language == "vi":
        return (
            "Decide the speakers' relationship from context and pick Vietnamese forms of address that fit it "
            "(e.g. em/anh for a couple, con/bố mẹ, tôi/bạn, mình/cậu). Keep the same pair for the whole video; "
            "follow the previous context when it is given. Do not switch to tôi/bạn mid-conversation."
        )
    return "Resolve pronouns and the speakers' relationship from context and keep forms of address consistent."


_EXAMPLES = {
    ("zh-CN", "vi"): (
        [
            {"id": 1, "text": "你怎么又迟到了啊", "seconds": 1.8},
            {"id": 2, "text": "堵车嘛，我也没办法", "seconds": 2.0},
            {"id": 3, "text": "这家店真的yyds", "seconds": 1.5},
        ],
        [
            {"id": 1, "text": "Sao anh lại đến muộn nữa vậy"},
            {"id": 2, "text": "Kẹt xe mà, anh cũng chịu thôi"},
            {"id": 3, "text": "Quán này đỉnh thật sự"},
        ],
    ),
}


def system_prompt(source_language: str, target_language: str, sample: str = "") -> str:
    source = language_name(source_language, sample)
    target = language_name(target_language)
    rules = [
        f"You are an expert video subtitle and dubbing translator from {source} to {target}.",
        "Rules:",
        "1. Translate every item. Return exactly one translation per id, in the same order. "
        "Never merge, split, skip or reorder items, even when a sentence continues into the next item.",
        "2. " + _address_rule(target_language),
        "3. " + _length_rule(target_language),
        "4. Translate meaning, tone and emotion, not word by word. Render internet slang and idioms with a natural "
        f"{target} equivalent. Do not translate filler particles (啊, 呢, 吧, 嘛, 哦) literally; carry their feeling "
        "through wording instead.",
        "5. Keep names consistent. Chinese person names become their Sino-Vietnamese reading when translating to "
        "Vietnamese (王小明 → Vương Tiểu Minh); keep brand names as written.",
        f"6. Output only {target}. Never leave Chinese characters, pinyin, notes, brackets or explanations.",
        '7. Reply with JSON only: {"translations": [{"id": <id>, "text": "<translation>"}]}.',
    ]
    example = _EXAMPLES.get((source_language, target_language))
    if example:
        source_items, translated_items = example
        rules.append(
            "Example input: " + json.dumps(source_items, ensure_ascii=False)
            + "\nExample output: " + json.dumps({"translations": translated_items}, ensure_ascii=False)
        )
    return "\n".join(rules)


def user_prompt(
    lines: Sequence[str],
    durations: Sequence[float | None] | None,
    history: Sequence[tuple[str, str]] = (),
) -> str:
    items: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        item: dict[str, Any] = {"id": index + 1, "text": line}
        seconds = durations[index] if durations and index < len(durations) else None
        if seconds:
            item["seconds"] = round(float(seconds), 1)
        items.append(item)
    parts = []
    if history:
        context = [{"source": source, "translation": translation} for source, translation in history]
        parts.append(
            "Previous lines of this video, already translated (context only, do not output them): "
            + json.dumps(context, ensure_ascii=False)
        )
    parts.append(f"Translate these {len(items)} items: " + json.dumps(items, ensure_ascii=False))
    return "\n".join(parts)


def parse_translations(text: str, count: int, *, provider: str) -> list[str]:
    """Accept {"translations": [{id, text}]}, {"translations": [str]} or a bare list."""

    value = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    try:
        payload = json.loads(value)
    except ValueError as exc:
        raise TranslationServiceError(f"{provider}_invalid_translation_json") from exc
    items = payload.get("translations") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise TranslationServiceError(f"{provider}_invalid_translation_array")
    if items and all(isinstance(item, dict) for item in items):
        by_id: dict[int, str] = {}
        for item in items:
            try:
                by_id[int(item["id"])] = str(item["text"])
            except (KeyError, TypeError, ValueError) as exc:
                raise TranslationServiceError(f"{provider}_invalid_translation_array") from exc
        if sorted(by_id) != list(range(1, count + 1)):
            raise TranslationServiceError(f"{provider}_translation_count_mismatch")
        translated = [by_id[index] for index in range(1, count + 1)]
    elif all(isinstance(item, str) for item in items):
        translated = list(items)
    else:
        raise TranslationServiceError(f"{provider}_invalid_translation_array")
    if len(translated) != count:
        raise TranslationServiceError(f"{provider}_translation_count_mismatch")
    if not all(item.strip() for item in translated):
        raise TranslationServiceError(f"{provider}_invalid_translation_array")
    return [item.strip() for item in translated]


class TranslationHistory:
    """Last translated cues of the current job, fed back as read-only context."""

    def __init__(self, size: int = HISTORY_SIZE):
        self._items: deque[tuple[str, str]] = deque(maxlen=size)

    def items(self) -> list[tuple[str, str]]:
        return list(self._items)

    def extend(self, sources: Sequence[str], translations: Sequence[str]) -> None:
        self._items.extend(zip(sources, translations))
