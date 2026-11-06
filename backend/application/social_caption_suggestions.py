"""Grounded AI caption variants with deterministic local fallback."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from infrastructure.llm_providers import LLMError, StoryLLM, create_story_llm
from pipeline.tiktok import LocalExtractiveTikTokProvider

logger = logging.getLogger(__name__)

MAX_CAPTION_CHARS = 350
MAX_HASHTAGS = 5
VALID_TONES = {"auto", "natural", "curious", "emotional", "expert"}
TONE_LABELS = {
    "auto": "tự chọn góc viết phù hợp",
    "natural": "tự nhiên, gần gũi",
    "curious": "gợi tò mò nhưng không giật tít sai",
    "emotional": "cảm xúc, chân thành",
    "expert": "rõ ràng, có chiều sâu",
}
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_SPACE_RE = re.compile(r"\s+")

SYSTEM_PROMPT = (
    "Bạn là biên tập viên nội dung video ngắn bằng tiếng Việt. Chỉ được viết từ các cue nguồn được cung cấp, "
    "không thêm tên người, số liệu, giá, khuyến mãi, sự kiện hoặc kết luận không có trong nguồn. "
    "Tạo đúng 3 biến thể có góc viết khác nhau, tự nhiên, không sao chép nguyên cả transcript. "
    "Hashtag chỉ được chọn từ allowed_hashtags; không gọi hashtag nào là xu hướng realtime. "
    "Mỗi biến thể phải dẫn source_cues là các chỉ số cue thực sự dùng. Trả JSON đúng schema."
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "variants": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "hook": {"type": "string"},
                    "body": {"type": "string"},
                    "cta": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "tone": {"type": "string"},
                    "reason": {"type": "string"},
                    "source_cues": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["hook", "body", "cta", "hashtags", "tone", "reason", "source_cues"],
            },
        }
    },
    "required": ["variants"],
}


@dataclass(frozen=True)
class CaptionVariant:
    caption: str
    hook: str
    body: str
    cta: str
    hashtags: list[str]
    tone: str
    reason: str
    source_cues: list[int]


@dataclass(frozen=True)
class CaptionSuggestions:
    variants: list[CaptionVariant]
    generator: str
    fallback: bool

    @property
    def caption(self) -> str:
        return self.variants[0].caption

    def as_dict(self) -> dict[str, Any]:
        return {
            "caption": self.caption,
            "generator": self.generator,
            "fallback": self.fallback,
            "variants": [asdict(variant) for variant in self.variants],
        }


def configured_caption_llms() -> list[StoryLLM]:
    providers: list[StoryLLM] = []
    for key in ("gemini", "qwen3"):
        try:
            provider = create_story_llm(key)
        except LLMError:
            continue
        if provider is not None:
            providers.append(provider)
    return providers


def _clean(value: Any, limit: int) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()[:limit]


def _local_variant(segments: Sequence[dict[str, Any]]) -> CaptionVariant:
    post = LocalExtractiveTikTokProvider().generate(segments, max_summary_chars=280, hashtag_count=5)
    paragraphs = post.caption.split("\n\n")
    content = paragraphs[0] if paragraphs else post.caption
    cta = paragraphs[-2] if len(paragraphs) >= 2 and paragraphs[-1].startswith("#") else ""
    return CaptionVariant(
        caption=post.caption,
        hook=post.hook,
        body=content,
        cta=cta,
        hashtags=post.hashtags,
        tone="local",
        reason="Bản ổn định được trích từ nội dung đã dịch.",
        source_cues=list(range(min(len(segments), 2))),
    )


def _user_prompt(segments: Sequence[dict[str, Any]], tone: str, allowed_hashtags: Sequence[str]) -> str:
    cues = [{"index": index, "text": _clean(segment.get("text"), 500)} for index, segment in enumerate(segments)]
    return "\n".join([
        f"Phong cách ưu tiên: {TONE_LABELS[tone]}.",
        f"Caption tối đa {MAX_CAPTION_CHARS} ký tự, tối đa {MAX_HASHTAGS} hashtag.",
        "allowed_hashtags: " + json.dumps(list(allowed_hashtags), ensure_ascii=False),
        "CUES:",
        json.dumps(cues, ensure_ascii=False),
    ])


def _parse_variants(raw: str, segments: Sequence[dict[str, Any]], allowed_hashtags: Sequence[str]) -> list[CaptionVariant]:
    payload = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    entries = payload.get("variants") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError("AI response has no variants")
    source_text = " ".join(_clean(segment.get("text"), 1000) for segment in segments)
    source_numbers = set(_NUMBER_RE.findall(source_text))
    allowed = set(allowed_hashtags)
    variants: list[CaptionVariant] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hook = _clean(entry.get("hook"), 120)
        body = _clean(entry.get("body"), 220)
        cta = _clean(entry.get("cta"), 100)
        reason = _clean(entry.get("reason"), 160)
        tone = _clean(entry.get("tone"), 40) or "ai"
        cue_indexes = entry.get("source_cues")
        if not isinstance(cue_indexes, list) or not cue_indexes:
            continue
        source_cues = list(dict.fromkeys(index for index in cue_indexes if isinstance(index, int)))
        if len(source_cues) != len(cue_indexes) or any(index < 0 or index >= len(segments) for index in source_cues):
            continue
        requested_tags = entry.get("hashtags") if isinstance(entry.get("hashtags"), list) else []
        hashtags = list(dict.fromkeys(str(tag).strip().lower() for tag in requested_tags if str(tag).strip().lower() in allowed))[:MAX_HASHTAGS]
        content = " ".join(part for part in (hook, body, cta) if part)
        if not hook or not body or not content or not set(_NUMBER_RE.findall(content)) <= source_numbers:
            continue
        caption_parts = ["\n".join(part for part in (hook, body) if part), cta]
        if hashtags:
            caption_parts.append(" ".join(hashtags))
        caption = "\n\n".join(part for part in caption_parts if part)
        normalized = _clean(caption, MAX_CAPTION_CHARS + 1).lower()
        if len(caption) > MAX_CAPTION_CHARS or normalized in seen:
            continue
        seen.add(normalized)
        variants.append(CaptionVariant(caption, hook, body, cta, hashtags, tone, reason, source_cues))
        if len(variants) == 3:
            break
    if len(variants) != 3:
        raise ValueError("AI must return three safe caption variants")
    return variants


def suggest_social_captions(
    segments: Sequence[dict[str, Any]], *, tone: str = "auto", llms: Sequence[StoryLLM] = (),
) -> CaptionSuggestions:
    if tone not in VALID_TONES:
        raise ValueError("tone must be auto, natural, curious, emotional, or expert")
    local = _local_variant(segments)
    allowed_hashtags = local.hashtags
    prompt = _user_prompt(segments, tone, allowed_hashtags)
    for llm in llms:
        try:
            variants = _parse_variants(llm.generate_json(SYSTEM_PROMPT, prompt, RESPONSE_SCHEMA), segments, allowed_hashtags)
            return CaptionSuggestions(variants=variants, generator=llm.name, fallback=False)
        except (LLMError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            logger.warning("Caption suggestion provider %s failed validation: %s", getattr(llm, "name", "unknown"), type(exc).__name__)
    return CaptionSuggestions(variants=[local], generator="local-extractive-v4", fallback=True)

