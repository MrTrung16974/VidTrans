"""AI suggestions of library music for a photo-post slideshow.

The model reads the post's caption (and, with Gemini, a few of its pictures) and the
library's titles and moods, then names the tracks that fit with a short reason. Without a
model, or when it fails, the caption's mood words pick tracks of the matching category so
the user still gets a useful answer.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from domain.music_categories import OTHER, guess_category
from infrastructure.llm_providers import LLMError, PromptImage, StoryLLM

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 5
MAX_PROMPT_TRACKS = 300
MAX_PROMPT_IMAGES = 4
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_REASON_CHARS = 160
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

SYSTEM_PROMPT = (
    "Bạn là biên tập viên âm nhạc cho video ngắn TikTok/Douyin. Dựa vào nội dung bài đăng ảnh, "
    "hãy chọn từ THƯ VIỆN NHẠC (chỉ được dùng track_id có trong danh sách) những bài hợp nhất về "
    "cảm xúc, nhịp độ và chủ đề. Xếp bài hợp nhất lên đầu. Lý do viết bằng tiếng Việt, một câu ngắn. "
    'Trả về JSON: {"mood": "<cảm xúc chung của bài đăng>", '
    '"suggestions": [{"track_id": "...", "reason": "..."}]}'
)
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mood": {"type": "string"},
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"track_id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["track_id", "reason"],
            },
        },
    },
    "required": ["suggestions"],
}


@dataclass(frozen=True)
class MusicSuggestions:
    items: list[dict[str, Any]]
    mood: str
    source: str  # the model's name, or "heuristic"

    def as_json(self) -> dict[str, Any]:
        return {"items": self.items, "mood": self.mood, "source": self.source}


def _suggestion(track: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "track_id": track["track_id"],
        "title": track["title"],
        "category": track.get("category") or OTHER,
        "duration_seconds": track["duration_seconds"],
        "reason": " ".join(reason.split())[:MAX_REASON_CHARS],
    }


def prompt_images(paths: Sequence[Path]) -> list[PromptImage]:
    """Up to a few of the post's pictures, spread over the set, that a model can read."""
    readable = [p for p in paths if p.suffix.lower() in IMAGE_TYPES and p.is_file()
                and p.stat().st_size <= MAX_IMAGE_BYTES]
    if len(readable) > MAX_PROMPT_IMAGES:
        step = len(readable) / MAX_PROMPT_IMAGES
        readable = [readable[int(index * step)] for index in range(MAX_PROMPT_IMAGES)]
    return [(IMAGE_TYPES[p.suffix.lower()], p.read_bytes()) for p in readable]


def heuristic_suggestions(caption: str, tracks: Sequence[dict[str, Any]]) -> MusicSuggestions:
    """Tracks of the mood the caption's words point to; nothing when the caption says no mood."""
    category = guess_category(caption, default=None)
    if category is None:
        return MusicSuggestions(items=[], mood="", source="heuristic")
    matching = [t for t in tracks if (t.get("category") or OTHER) == category][:MAX_SUGGESTIONS]
    reason = f"Nội dung bài đăng mang không khí “{category}”."
    return MusicSuggestions(items=[_suggestion(t, reason) for t in matching], mood=category, source="heuristic")


def _user_prompt(caption: str, image_count: int, tracks: Sequence[dict[str, Any]], with_images: bool) -> str:
    library = [
        {"track_id": t["track_id"], "title": t["title"], "category": t.get("category") or OTHER,
         "seconds": round(float(t["duration_seconds"]))}
        for t in tracks[:MAX_PROMPT_TRACKS]
    ]
    lines = [
        f"Bài đăng gồm {image_count} ảnh.",
        f"Caption / hashtag: {caption.strip() or '(không có)'}",
    ]
    if with_images:
        lines.append("Một vài ảnh của bài đăng được gửi kèm ở trên.")
    lines += [
        f"Hãy gợi ý tối đa {MAX_SUGGESTIONS} bài.",
        "THƯ VIỆN NHẠC:",
        json.dumps(library, ensure_ascii=False),
    ]
    return "\n".join(lines)


def _parse(raw: str, tracks: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    data = json.loads(raw)
    by_id = {t["track_id"]: t for t in tracks}
    items: list[dict[str, Any]] = []
    for entry in data.get("suggestions") or []:
        if not isinstance(entry, dict):
            continue
        track = by_id.get(str(entry.get("track_id") or "").strip())
        if track is None or any(item["track_id"] == track["track_id"] for item in items):
            continue  # A made-up or repeated id.
        items.append(_suggestion(track, str(entry.get("reason") or "")))
        if len(items) >= MAX_SUGGESTIONS:
            break
    return items, " ".join(str(data.get("mood") or "").split())[:80]


def suggest_music(
    *,
    caption: str,
    image_paths: Sequence[Path],
    tracks: Sequence[dict[str, Any]],
    llm: StoryLLM | None,
) -> MusicSuggestions:
    """Library tracks that fit the post, best first."""
    if not tracks:
        return MusicSuggestions(items=[], mood="", source="heuristic")
    if llm is not None:
        images = prompt_images(image_paths) if getattr(llm, "accepts_images", False) else []
        user = _user_prompt(caption, len(image_paths), tracks, bool(images))
        try:
            raw = (llm.generate_json(SYSTEM_PROMPT, user, RESPONSE_SCHEMA, images=images) if images
                   else llm.generate_json(SYSTEM_PROMPT, user, RESPONSE_SCHEMA))
            items, mood = _parse(raw, tracks)
            if items:
                return MusicSuggestions(items=items, mood=mood, source=llm.name)
            logger.info("Music suggestion model %s named no library track", llm.name)
        except (LLMError, ValueError, TypeError, AttributeError) as exc:
            logger.warning("Music suggestion by %s failed: %s", llm.name, exc)
    return heuristic_suggestions(caption, tracks)
