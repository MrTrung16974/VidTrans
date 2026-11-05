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
from difflib import SequenceMatcher
from typing import Any, Sequence

from infrastructure.vietnamese_translator import TranslationServiceError


class TranslationOutputError(TranslationServiceError):
    """The model answered, but the answer is unusable for these lines (bad JSON, wrong count,
    lines shifted). A smaller batch usually fixes it, unlike rate limits or outages."""

    bad_output = True

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
            {"id": 1, "source": "你怎么又迟到了啊", "text": "Sao anh lại đến muộn nữa vậy"},
            {"id": 2, "source": "堵车嘛，我也没办法", "text": "Kẹt xe mà, anh cũng chịu thôi"},
            {"id": 3, "source": "这家店真的yyds", "text": "Quán này đỉnh thật sự"},
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
        '7. Reply with JSON only: {"translations": [{"id": <id>, "source": "<the item text copied exactly>", '
        '"text": "<translation>"}]}. "source" must be the text of that same id, so each translation stays '
        "on its own line; translate song lyrics and fragments too instead of skipping them.",
        "8. The items come from speech recognition, which mishears near-homophones (我没吐 heard for 我没赌 in "
        "a gambling story, 公报自愁 for 公报私仇, 手续 for 手气). When an item makes no sense as written, "
        "translate what was most plausibly said given the context, never the nonsense reading. "
        "\"source\" still echoes the item exactly as given.",
        "9. Say numbers the way a native speaker would (七七 → bảy bảy, 五百万 → năm triệu); "
        "Sino-Vietnamese readings are for names only.",
        "10. When a glossary is given, use its renderings for those names and terms every time they occur.",
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
    glossary: Sequence[dict[str, str]] = (),
) -> str:
    items: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        item: dict[str, Any] = {"id": index + 1, "text": line}
        seconds = durations[index] if durations and index < len(durations) else None
        if seconds:
            item["seconds"] = round(float(seconds), 1)
        items.append(item)
    parts = []
    if glossary:
        parts.append(
            "Glossary for this video (names and terms with their fixed translation; "
            "\"heard_as\" lists misheard spellings of the same term): "
            + json.dumps(list(glossary), ensure_ascii=False)
        )
    if history:
        context = [{"source": source, "translation": translation} for source, translation in history]
        parts.append(
            "Previous lines of this video, already translated (context only, do not output them): "
            + json.dumps(context, ensure_ascii=False)
        )
    parts.append(f"Translate these {len(items)} items: " + json.dumps(items, ensure_ascii=False))
    return "\n".join(parts)


GLOSSARY_MAX_TERMS = 40
GLOSSARY_MAX_TRANSCRIPT_CHARS = 8000


def glossary_system_prompt(source_language: str, target_language: str, sample: str = "") -> str:
    source = language_name(source_language, sample)
    target = language_name(target_language)
    vietnamese = (
        " Chinese person names take their Sino-Vietnamese reading (王小明 → Vương Tiểu Minh)."
        if target_language == "vi" else ""
    )
    return "\n".join([
        f"You prepare a translation glossary for one video, translated from {source} to {target}.",
        "The transcript comes from speech recognition: the same name is often spelt differently in "
        "different lines (桑言 / 桑延, 华为宏 / 华为红 / 华慧红), and a kinship word or a brand can be "
        "misheard as something else (季妹 for 堂妹, 美帝 for 美的).",
        "Rules:",
        "1. List the person names, nicknames, places, brands, product and show titles, and recurring "
        f"special terms. Give each one {target} rendering to use everywhere.{vietnamese} Keep brands as "
        "their usual written form.",
        "2. Put the most plausible correct spelling in \"source\" and every other spelling found in the "
        "transcript for the same thing in \"heard_as\".",
        f"3. Only list terms that occur in the transcript; at most {GLOSSARY_MAX_TERMS} entries. "
        "Ordinary words do not belong in the glossary.",
        '4. Reply with JSON only: {"terms": [{"source": "<term>", "heard_as": ["<other spelling>"], '
        '"target": "<translation>"}]}.',
    ])


def glossary_user_prompt(lines: Sequence[str]) -> str:
    text = "\n".join(lines)[:GLOSSARY_MAX_TRANSCRIPT_CHARS]
    return "Transcript, one line per subtitle:\n" + text


def parse_glossary(text: str) -> list[dict[str, Any]]:
    """The glossary entries of a model reply; anything malformed is left out."""

    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        payload = json.loads(value)
    except ValueError:
        return []
    items = payload.get("terms") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    terms: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        if not source or not target or len(source) > 40 or len(target) > 60:
            continue
        heard = [
            str(value).strip() for value in item.get("heard_as") or []
            if isinstance(value, str) and str(value).strip() and str(value).strip() != source
        ][:5]
        entry: dict[str, Any] = {"source": source, "target": target}
        if heard:
            entry["heard_as"] = heard
        terms.append(entry)
    return terms[:GLOSSARY_MAX_TERMS]


def glossary_for_lines(glossary: Sequence[dict[str, Any]], lines: Sequence[str]) -> list[dict[str, Any]]:
    """Entries that occur in these lines, so each batch prompt stays short."""

    joined = "".join(lines)
    return [
        entry for entry in glossary
        if any(spelling and spelling in joined for spelling in [entry["source"], *entry.get("heard_as", [])])
    ]


def condense_system_prompt(source_language: str, target_language: str, sample: str = "") -> str:
    source = language_name(source_language, sample)
    target = language_name(target_language)
    return "\n".join([
        f"You shorten {target} dubbing lines translated from {source} so the voice fits the original timing.",
        "Rules:",
        "1. Each item has the original `source`, the current `text` and `max_syllables`. Rewrite `text` so it "
        "keeps the meaning, tone and forms of address of the source but has at most `max_syllables` spoken "
        "syllables (words). Drop filler, repetition and details the scene does not need.",
        "2. If `text` still contains source-script characters, translate them; Chinese person names become their "
        "Sino-Vietnamese reading (王小明 → Vương Tiểu Minh).",
        f"3. Output only natural spoken {target}: no notes, brackets, pinyin or source-script characters.",
        '4. Reply with JSON only: {"translations": [{"id": <id>, "source": "<the item source copied exactly>", '
        '"text": "<shortened line>"}]}, one entry per id, in order.',
    ])


def condense_user_prompt(
    sources: Sequence[str], translations: Sequence[str], max_syllables: Sequence[int]
) -> str:
    items = [
        {"id": index + 1, "source": source, "text": text, "max_syllables": int(limit)}
        for index, (source, text, limit) in enumerate(zip(sources, translations, max_syllables))
    ]
    return f"Shorten these {len(items)} items: " + json.dumps(items, ensure_ascii=False)


def proofread_system_prompt(language: str, *, sung: bool) -> str:
    name = language_name(language)
    kind = "song lyrics" if sung else "speech"
    return "\n".join([
        f"You proofread {name} subtitles produced by speech recognition (Whisper) from {kind}.",
        "Whisper mishears words: near-homophones, wrong tones or diacritics, split or merged words, "
        "and in songs the singing and music blur the words.",
        "Rules:",
        "1. Fix only words that were clearly misheard, plus spelling, diacritics and punctuation. "
        "Keep every correct word exactly as it is.",
        "2. Read all lines together: they are consecutive, so use the context of the whole text to decide "
        "what was really said or sung." + (
            " If you recognise a well-known song, use its real lyrics for these lines." if sung else ""
        ),
        "3. Never translate, summarise, restyle, add or remove lines, or move words between lines: "
        "each output line must cover the same words as its input line, because its timing is fixed.",
        "4. When unsure, return the line unchanged.",
        '5. Reply with JSON only: {"translations": [{"id": <id>, "source": "<the input line copied exactly>", '
        '"text": "<corrected line>"}]}, one entry per id, in order.',
    ])


def proofread_user_prompt(lines: Sequence[str]) -> str:
    items = [{"id": index + 1, "text": line} for index, line in enumerate(lines)]
    return f"Proofread these {len(items)} consecutive lines: " + json.dumps(items, ensure_ascii=False)


def rebalance_system_prompt(language: str) -> str:
    name = language_name(language)
    return "\n".join([
        f"You fix line breaks in {name} subtitles produced by speech recognition.",
        "The lines are consecutive speech. Their breaks were placed by audio timing, which is often off by "
        "one to three characters, so a break can fall inside a word or phrase: the last character of a "
        "sentence starts the next line (像极了他追查多年的通缉 / 犯虽然已经过去了十多年), or the first word of "
        "a sentence ends the previous line (日夜 / 里深深刻进了她的脑海 → 日夜里 / 深深刻进了她的脑海).",
        "Rules:",
        "1. Move at most three characters across each break so that every line ends where a word, phrase "
        "or sentence naturally ends. Leave a break that is already natural exactly where it is.",
        "2. Never change, add, remove, correct or reorder any character, even a misheard one, and never "
        "add punctuation or spaces: joined together, your lines must equal the input lines joined together.",
        "3. Keep the same number of lines, in the same order; no line may become empty.",
        '4. Reply with JSON only: {"translations": [{"id": <id>, "text": "<line with fixed breaks>"}]}, '
        "one entry per id, in order.",
    ])


def rebalance_user_prompt(lines: Sequence[str]) -> str:
    items = [{"id": index + 1, "text": line} for index, line in enumerate(lines)]
    return f"Fix the line breaks of these {len(items)} consecutive lines: " + json.dumps(items, ensure_ascii=False)


def _comparable(text: str) -> str:
    """Letters and digits only, Simplified script, case-folded: what an echo must preserve."""

    table = str.maketrans(_TRADITIONAL, _SIMPLIFIED)
    return "".join(character for character in str(text).translate(table).casefold() if character.isalnum())


def _same_line(sent: str, echoed: str) -> bool:
    sent, echoed = _comparable(sent), _comparable(echoed)
    if not sent or not echoed:
        return sent == echoed
    if sent in echoed or echoed in sent:
        return min(len(sent), len(echoed)) / max(len(sent), len(echoed)) >= 0.5
    return SequenceMatcher(None, sent, echoed).ratio() >= 0.6


def parse_translations(
    text: str, count: int, *, provider: str, sources: Sequence[str] | None = None
) -> list[str]:
    """Accept {"translations": [{id, source?, text}]}, {"translations": [str]} or a bare list.

    With ``sources``, a model that drops one line and shifts the rest up (padding the
    end with a repeat) is caught: echoed sources must match, and two different source
    lines may not come back as the same adjacent translation.
    """

    value = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    try:
        payload = json.loads(value)
    except ValueError as exc:
        raise TranslationOutputError(f"{provider}_invalid_translation_json") from exc
    items = payload.get("translations") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise TranslationOutputError(f"{provider}_invalid_translation_array")
    echoes: dict[int, str] = {}
    if items and all(isinstance(item, dict) for item in items):
        by_id: dict[int, str] = {}
        for item in items:
            try:
                by_id[int(item["id"])] = str(item["text"])
            except (KeyError, TypeError, ValueError) as exc:
                raise TranslationOutputError(f"{provider}_invalid_translation_array") from exc
            if isinstance(item.get("source"), str):
                echoes[int(item["id"])] = item["source"]
        if sorted(by_id) != list(range(1, count + 1)):
            raise TranslationOutputError(f"{provider}_translation_count_mismatch")
        translated = [by_id[index] for index in range(1, count + 1)]
    elif all(isinstance(item, str) for item in items):
        translated = list(items)
    else:
        raise TranslationOutputError(f"{provider}_invalid_translation_array")
    if len(translated) != count:
        raise TranslationOutputError(f"{provider}_translation_count_mismatch")
    if not all(item.strip() for item in translated):
        raise TranslationOutputError(f"{provider}_invalid_translation_array")
    translated = [item.strip() for item in translated]
    if sources is not None:
        for index, echoed in echoes.items():
            if not _same_line(sources[index - 1], echoed):
                raise TranslationOutputError(f"{provider}_translation_misaligned")
        for index in range(1, count):
            if (
                _comparable(translated[index]) == _comparable(translated[index - 1])
                and not _same_line(sources[index], sources[index - 1])
            ):
                raise TranslationOutputError(f"{provider}_translation_misaligned")
    return translated


class TranslationHistory:
    """Last translated cues of the current job, fed back as read-only context."""

    def __init__(self, size: int = HISTORY_SIZE):
        self._items: deque[tuple[str, str]] = deque(maxlen=size)

    def items(self) -> list[tuple[str, str]]:
        return list(self._items)

    def extend(self, sources: Sequence[str], translations: Sequence[str]) -> None:
        self._items.extend(zip(sources, translations))
