from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence


_TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_SENTENCE_END_RE = re.compile(r"[.!?…]$")

_STOP_WORDS = {
    "ai",
    "anh",
    "bạn",
    "bị",
    "bởi",
    "các",
    "cái",
    "cho",
    "chúng",
    "chỉ",
    "có",
    "của",
    "cũng",
    "đã",
    "đang",
    "đây",
    "đến",
    "để",
    "đi",
    "đó",
    "được",
    "em",
    "gì",
    "hay",
    "họ",
    "khi",
    "không",
    "là",
    "lại",
    "làm",
    "mà",
    "mình",
    "một",
    "này",
    "nên",
    "nếu",
    "người",
    "những",
    "như",
    "nhưng",
    "nó",
    "ở",
    "phải",
    "ra",
    "rằng",
    "rất",
    "sẽ",
    "sự",
    "ta",
    "tại",
    "thì",
    "trên",
    "trong",
    "tôi",
    "từ",
    "và",
    "vẫn",
    "về",
    "vì",
    "với",
}


# Conservative phrase vocabulary: tags are emitted only when the exact phrase
# appears in a usable translation. Ordering favors topics over generic themes.
_HASHTAG_PHRASES = (
    "nấu ăn", "ẩm thực", "công thức", "du lịch", "chăm sóc da", "trang điểm",
    "thời trang", "tập luyện", "thể thao", "bóng đá", "sức khỏe", "dinh dưỡng",
    "nuôi dạy con", "gia đình", "tình yêu", "tình bạn", "hôn nhân", "cha mẹ",
    "học tập", "học tiếng trung", "công việc", "kinh doanh", "khởi nghiệp",
    "phát triển bản thân", "kỷ luật", "kiên trì", "mục tiêu", "lựa chọn",
    "cuộc sống", "cuộc đời", "tương lai", "thành công", "hạnh phúc",
)


@dataclass(frozen=True)
class TikTokPost:
    title: str
    hook: str
    summary: str
    caption: str
    keywords: list[str]
    hashtags: list[str]
    source_cues: int
    generator: str = "local-extractive-v2"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["summary_characters"] = len(self.summary)
        payload["caption_characters"] = len(self.caption)
        return payload


class TikTokSummaryProvider(Protocol):
    def generate(
        self,
        segments: Sequence[dict[str, Any]],
        *,
        max_summary_chars: int,
        hashtag_count: int,
    ) -> TikTokPost: ...


@dataclass(frozen=True)
class _Sentence:
    index: int
    text: str
    tokens: tuple[str, ...]
    needs_review: bool


def _clean_text(value: str) -> str:
    return _SPACE_RE.sub(" ", value or "").strip()


def _tokenize(value: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(value)]


def _content_tokens(value: str) -> list[str]:
    return [
        token
        for token in _tokenize(value)
        if len(token) > 1 and token not in _STOP_WORDS and not token.isdigit()
    ]


def _truncate_at_word(value: str, limit: int) -> str:
    value = _clean_text(value)
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    shortened = value[: limit - 1].rstrip()
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip(" ,;:-") + "…"


def _ensure_sentence_end(value: str) -> str:
    value = _clean_text(value)
    if not value or _SENTENCE_END_RE.search(value):
        return value
    return value + "."


def _ascii_hashtag(value: str) -> str:
    value = value.replace("Đ", "D").replace("đ", "d")
    value = unicodedata.normalize("NFD", value)
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    value = re.sub(r"[^0-9A-Za-z]+", "", value)
    return f"#{value.lower()}" if value else ""


def _unique_sentences(segments: Sequence[dict[str, Any]]) -> list[_Sentence]:
    sentences: list[_Sentence] = []
    seen: set[str] = set()
    for segment in segments:
        text = _clean_text(str(segment.get("text") or ""))
        normalized = " ".join(_tokenize(text))
        if not text or normalized in seen:
            continue
        seen.add(normalized)
        sentences.append(
            _Sentence(
                index=len(sentences),
                text=text,
                tokens=tuple(_content_tokens(text)),
                needs_review=bool(segment.get("needs_review")),
            )
        )
    return sentences


class LocalExtractiveTikTokProvider:
    """Deterministic Vietnamese post generator without network dependencies."""

    generator_name = "local-extractive-v2"

    def generate(
        self,
        segments: Sequence[dict[str, Any]],
        *,
        max_summary_chars: int = 350,
        hashtag_count: int = 6,
    ) -> TikTokPost:
        if not 80 <= max_summary_chars <= 1500:
            raise ValueError("max_summary_chars must be between 80 and 1500")
        if not 0 <= hashtag_count <= 12:
            raise ValueError("hashtag_count must be between 0 and 12")

        sentences = _unique_sentences(segments)
        if not sentences:
            raise ValueError("Cannot create TikTok content from an empty translation")

        source_cues = len(sentences)
        # Uncertain translations must not become confident marketing copy when
        # usable translations exist. Keep the original cue count for metadata.
        reliable = [sentence for sentence in sentences if not sentence.needs_review]
        sentences = reliable or sentences
        frequencies = Counter(token for sentence in sentences for token in sentence.tokens)
        first_seen: dict[str, int] = {}
        for sentence in sentences:
            for token in sentence.tokens:
                first_seen.setdefault(token, sentence.index)

        def score(sentence: _Sentence) -> float:
            tokens = set(sentence.tokens)
            lexical_score = sum(math.log1p(frequencies[token]) for token in tokens)
            length_penalty = math.sqrt(max(len(sentence.tokens), 1))
            position_bonus = 0.35 / (sentence.index + 1)
            review_factor = 0.65 if sentence.needs_review else 1.0
            return ((lexical_score / length_penalty) + position_bonus) * review_factor

        ranked = sorted(sentences, key=lambda sentence: (-score(sentence), sentence.index))
        # Favor complete, readable statements over clipped subtitle fragments.
        readable = [sentence for sentence in ranked
                    if 5 <= len(_tokenize(sentence.text)) <= 32
                    and not re.match(r"^(?:và|nhưng|bởi vì|cho nên|vì vậy|sau đó)\b", sentence.text, re.I)]
        hook_sentence = (readable or ranked)[0]
        hook = _truncate_at_word(hook_sentence.text, 110)
        title = _truncate_at_word(hook_sentence.text, 80)

        selected: list[_Sentence] = []
        used_characters = 0
        for sentence in ranked:
            if len(selected) >= 4:
                break
            rendered = _ensure_sentence_end(sentence.text)
            separator_size = 1 if selected else 0
            if used_characters + separator_size + len(rendered) <= max_summary_chars:
                selected.append(sentence)
                used_characters += separator_size + len(rendered)

        if not selected:
            summary = _truncate_at_word(_ensure_sentence_end(hook_sentence.text), max_summary_chars)
        else:
            selected.sort(key=lambda sentence: sentence.index)
            summary = " ".join(_ensure_sentence_end(sentence.text) for sentence in selected)

        ranked_keywords = sorted(
            frequencies,
            key=lambda token: (-frequencies[token], first_seen[token], token),
        )
        keywords = ranked_keywords[:8]

        # Match meaningful Vietnamese phrases, never isolated syllables such
        # as #quan or #trong, and never fill a quota with unrelated viral tags.
        source_text = " " + " ".join(_tokenize(" ".join(sentence.text for sentence in sentences))) + " "
        hashtags = []
        for phrase in _HASHTAG_PHRASES:
            if f" {phrase} " in source_text:
                hashtags.append(_ascii_hashtag(phrase))
        hashtags = list(dict.fromkeys(hashtags))[:min(hashtag_count, 5)]

        # Caption is its own short reading flow, not the four-sentence summary.
        # Keep adjacent source context and do not invent clickbait or a CTA.
        caption_limit = min(max_summary_chars, 350)
        opening = hook_sentence
        fitting = [sentence for sentence in sentences
                   if len(_ensure_sentence_end(sentence.text)) <= caption_limit
                   and len(_tokenize(sentence.text)) >= 5]
        if len(_ensure_sentence_end(opening.text)) > caption_limit and fitting:
            opening = fitting[0]
        opening_text = _truncate_at_word(_ensure_sentence_end(opening.text), caption_limit)
        caption_parts = [opening_text]
        for sentence in sentences:
            if sentence.index <= opening.index:
                continue
            overlap = len(set(opening.tokens) & set(sentence.tokens)) / max(1, len(set(sentence.tokens)))
            if overlap >= 0.75 or len(_tokenize(sentence.text)) < 5:
                continue
            detail = _ensure_sentence_end(sentence.text)
            if len(opening_text) + 2 + len(detail) <= caption_limit:
                caption_parts.append(detail)
            # Do not jump over context to assemble unrelated dialogue lines.
            break
        hook = _truncate_at_word(opening.text, 110)
        title = _truncate_at_word(opening.text, 80)
        if hashtags:
            caption_parts.append(" ".join(hashtags))
        caption = "\n\n".join(caption_parts)

        return TikTokPost(
            title=title,
            hook=hook,
            summary=summary,
            caption=caption,
            keywords=keywords,
            hashtags=hashtags,
            source_cues=source_cues,
            generator=self.generator_name,
        )


def render_tiktok_text(post: TikTokPost) -> str:
    hashtags = " ".join(post.hashtags)
    return "\n".join(
        [
            "TIÊU ĐỀ",
            post.title,
            "",
            "HOOK",
            post.hook,
            "",
            "TÓM TẮT",
            post.summary,
            "",
            "CAPTION",
            post.caption,
            "",
            "HASHTAG",
            hashtags,
        ]
    ).rstrip() + "\n"


def write_tiktok_artifacts(post: TikTokPost, output_dir: Path, job_id: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{job_id}.tiktok.json"
    text_path = output_dir / f"{job_id}.tiktok.txt"
    json_path.write_text(
        json.dumps(
            {"version": 1, **post.as_dict()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    text_path.write_text(render_tiktok_text(post), encoding="utf-8")
    return json_path, text_path
