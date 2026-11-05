"""AI-directed, strictly validated plans for photo-post slideshow videos."""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from application.music_suggestions import MAX_REASON_CHARS, heuristic_suggestions, prompt_images
from infrastructure.llm_providers import LLMError, StoryLLM

logger = logging.getLogger(__name__)

PLAN_VERSION = 1
STYLES = ("dynamic", "cinematic", "emotional", "clean", "travel", "product")
MOTIONS = ("slow_zoom_in", "slow_zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static")
TRANSITIONS = ("cut", "crossfade", "fade")
ROLES = ("hook", "story", "highlight", "ending")
MIN_SLIDE_SECONDS = 1.0
MAX_SLIDE_SECONDS = 8.0
MIN_PLAN_SECONDS = 5.0
MAX_PLAN_SECONDS = 10 * 60.0
MAX_MUSIC_SUGGESTIONS = 5

SYSTEM_PROMPT = (
    "Bạn là đạo diễn video ngắn. Hãy lập phương án dựng từ toàn bộ ảnh: chọn thứ tự tạo hook, "
    "nhịp thời lượng, chuyển động camera, chuyển cảnh và nhạc hợp cảm xúc. Chỉ dùng các enum, "
    "image_index và track_id được cung cấp. Không bỏ ảnh. Trả JSON đúng schema, không markdown."
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "style": {"type": "string", "enum": list(STYLES)},
        "mood": {"type": "string"},
        "summary": {"type": "string"},
        "confidence": {"type": "number"},
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "image_index": {"type": "integer"},
                    "duration_seconds": {"type": "number"},
                    "motion": {"type": "string", "enum": list(MOTIONS)},
                    "transition": {"type": "string", "enum": list(TRANSITIONS)},
                    "transition_seconds": {"type": "number"},
                    "role": {"type": "string", "enum": list(ROLES)},
                },
                "required": ["image_index", "duration_seconds", "motion", "transition", "role"],
            },
        },
        "music_suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "track_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["track_id", "reason"],
            },
        },
    },
    "required": ["style", "slides", "music_suggestions"],
}


@dataclass(frozen=True)
class SlideshowPlan:
    style: str
    mood: str
    summary: str
    target_seconds: float
    confidence: float
    keep_original_order: bool
    slides: list[dict[str, Any]]
    music_suggestions: list[dict[str, Any]]
    source: str

    def as_json(self) -> dict[str, Any]:
        return {
            "version": PLAN_VERSION,
            "source": self.source,
            "style": self.style,
            "mood": self.mood,
            "summary": self.summary,
            "target_seconds": self.target_seconds,
            "confidence": self.confidence,
            "keep_original_order": self.keep_original_order,
            "slides": self.slides,
            "music_suggestions": self.music_suggestions,
        }


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(low, min(high, number))


def suggested_seconds(image_count: int, requested: float | None = None) -> float:
    minimum = max(MIN_PLAN_SECONDS, image_count * MIN_SLIDE_SECONDS)
    maximum = min(MAX_PLAN_SECONDS, max(minimum, image_count * MAX_SLIDE_SECONDS))
    default = max(minimum, min(maximum, image_count * 4.0))
    return round(_clamp(requested, minimum, maximum, default), 2)


def _normalized_durations(weights: Sequence[float], target: float) -> list[float]:
    if not weights:
        return []
    safe = [max(MIN_SLIDE_SECONDS, min(MAX_SLIDE_SECONDS, float(value))) for value in weights]
    total = sum(safe)
    scaled = [value * target / total for value in safe]
    # The target is bounded by image_count * MAX_SLIDE_SECONDS; redistribute any clamped remainder.
    result = [max(MIN_SLIDE_SECONDS, min(MAX_SLIDE_SECONDS, value)) for value in scaled]
    for _ in range(len(result) * 2):
        delta = target - sum(result)
        if abs(delta) < 0.01:
            break
        candidates = [i for i, value in enumerate(result)
                      if (delta > 0 and value < MAX_SLIDE_SECONDS) or (delta < 0 and value > MIN_SLIDE_SECONDS)]
        if not candidates:
            break
        share = delta / len(candidates)
        for index in candidates:
            result[index] = max(MIN_SLIDE_SECONDS, min(MAX_SLIDE_SECONDS, result[index] + share))
    rounded = [round(value, 2) for value in result]
    rounded[-1] = round(rounded[-1] + target - sum(rounded), 2)
    return rounded


def _music(entries: Any, tracks: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(track["track_id"]): track for track in tracks}
    output: list[dict[str, Any]] = []
    for raw in entries if isinstance(entries, list) else []:
        if not isinstance(raw, dict):
            continue
        track = by_id.get(str(raw.get("track_id") or ""))
        if track is None or any(item["track_id"] == track["track_id"] for item in output):
            continue
        output.append({
            "track_id": track["track_id"],
            "title": track.get("title") or "",
            "category": track.get("category") or "Khác",
            "duration_seconds": float(track.get("duration_seconds") or 0),
            "reason": _clean(raw.get("reason"), MAX_REASON_CHARS),
            "score": round(_clamp(raw.get("score"), 0, 1, 0.5), 3),
        })
        if len(output) >= MAX_MUSIC_SUGGESTIONS:
            break
    return output


def parse_plan(
    raw: str | dict[str, Any], *, image_count: int, tracks: Sequence[dict[str, Any]],
    target_seconds: float, keep_original_order: bool, source: str, strict: bool = False,
) -> SlideshowPlan:
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        raise ValueError("Slideshow plan must be an object")
    if strict and int(data.get("version", -1)) != PLAN_VERSION:
        raise ValueError("Unsupported slideshow plan version")
    if strict and data.get("style") not in STYLES:
        raise ValueError("Invalid slideshow style")
    raw_slides = data.get("slides")
    if not isinstance(raw_slides, list) or len(raw_slides) != image_count:
        raise ValueError("Slideshow plan must contain every image once")
    slides_by_index: dict[int, dict[str, Any]] = {}
    for item in raw_slides:
        if not isinstance(item, dict) or isinstance(item.get("image_index"), bool):
            raise ValueError("Invalid slide")
        index = int(item.get("image_index", -1))
        if index < 0 or index >= image_count or index in slides_by_index:
            raise ValueError("Invalid or repeated image index")
        if strict and (item.get("motion") not in MOTIONS or item.get("transition") not in TRANSITIONS
                       or item.get("role") not in ROLES):
            raise ValueError("Invalid slideshow enum")
        slides_by_index[index] = item
    if set(slides_by_index) != set(range(image_count)):
        raise ValueError("Slideshow plan omitted an image")
    base_order = list(range(image_count)) if keep_original_order else [int(item["image_index"]) for item in raw_slides]
    passes = max(1, math.ceil(target_seconds / (image_count * MAX_SLIDE_SECONDS)))
    order = base_order * passes
    durations = _normalized_durations(
        [_clamp(slides_by_index[index].get("duration_seconds"), MIN_SLIDE_SECONDS, MAX_SLIDE_SECONDS, 4)
         for index in order], target_seconds,
    )
    slides: list[dict[str, Any]] = []
    for position, (index, duration) in enumerate(zip(order, durations)):
        item = slides_by_index[index]
        motion = str(item.get("motion") or "")
        transition = str(item.get("transition") or "")
        role = str(item.get("role") or "")
        slides.append({
            "image_index": index,
            "duration_seconds": duration,
            "motion": motion if motion in MOTIONS else MOTIONS[position % (len(MOTIONS) - 1)],
            "transition": "cut" if position == len(order) - 1 else (transition if transition in TRANSITIONS else "crossfade"),
            "transition_seconds": 0.0 if position == len(order) - 1 or transition == "cut" else round(
                _clamp(item.get("transition_seconds"), 0.1, min(0.8, duration / 2), 0.4), 2),
            "role": role if role in ROLES else ("hook" if position == 0 else "ending" if position == len(order) - 1 else "story"),
        })
    style = str(data.get("style") or "")
    return SlideshowPlan(
        style=style if style in STYLES else "clean",
        mood=_clean(data.get("mood"), 80),
        summary=_clean(data.get("summary"), 240),
        target_seconds=round(sum(item["duration_seconds"] for item in slides), 2),
        confidence=round(_clamp(data.get("confidence"), 0, 1, 0.5), 3),
        keep_original_order=keep_original_order,
        slides=slides,
        music_suggestions=_music(data.get("music_suggestions"), tracks),
        source=source,
    )


def heuristic_plan(
    *, caption: str, image_count: int, tracks: Sequence[dict[str, Any]], target_seconds: float,
    keep_original_order: bool, preferred_style: str = "auto",
) -> SlideshowPlan:
    text = caption.lower()
    if preferred_style in STYLES:
        style = preferred_style
    elif any(word in text for word in ("du lịch", "travel", "trip", "check-in")):
        style = "travel"
    elif any(word in text for word in ("sản phẩm", "product", "review", "shop")):
        style = "product"
    elif any(word in text for word in ("buồn", "nhớ", "chia tay", "cảm động")):
        style = "emotional"
    elif any(word in text for word in ("party", "tiệc", "vui", "nhảy")):
        style = "dynamic"
    else:
        style = "cinematic"
    motions = {
        "dynamic": ("pan_right", "slow_zoom_in", "pan_left", "slow_zoom_out"),
        "product": ("slow_zoom_in", "static", "pan_right", "static"),
        "emotional": ("slow_zoom_in", "pan_left", "slow_zoom_out", "pan_right"),
    }.get(style, ("slow_zoom_in", "pan_right", "slow_zoom_out", "pan_left"))
    raw = {
        "style": style,
        "mood": style,
        "summary": "Phương án an toàn theo nội dung caption và số lượng ảnh.",
        "confidence": 0.45,
        "slides": [
            {"image_index": index, "duration_seconds": target_seconds / image_count,
             "motion": motions[index % len(motions)], "transition": "crossfade", "transition_seconds": 0.4,
             "role": "hook" if index == 0 else "ending" if index == image_count - 1 else "story"}
            for index in range(image_count)
        ],
        "music_suggestions": [
            {"track_id": item["track_id"], "reason": item["reason"], "score": 0.6}
            for item in heuristic_suggestions(caption, tracks).items
        ],
    }
    return parse_plan(raw, image_count=image_count, tracks=tracks, target_seconds=target_seconds,
                      keep_original_order=keep_original_order, source="heuristic")


def _prompt(caption: str, image_count: int, tracks: Sequence[dict[str, Any]], target_seconds: float,
            keep_original_order: bool, preferred_style: str, with_images: bool) -> str:
    library = [{"track_id": item["track_id"], "title": item.get("title") or "",
                "category": item.get("category") or "Khác", "seconds": round(float(item.get("duration_seconds") or 0))}
               for item in tracks[:300]]
    return "\n".join([
        f"Caption/hashtag: {caption.strip() or '(không có)'}",
        f"Số ảnh: {image_count}; index hợp lệ: 0..{image_count - 1}.",
        f"Độ dài mục tiêu: {target_seconds:.2f} giây.",
        f"Giữ thứ tự gốc: {keep_original_order}.",
        f"Phong cách ưu tiên: {preferred_style}.",
        "Ảnh mẫu được gửi kèm." if with_images else "Không có ảnh mẫu; suy luận từ caption.",
        "Mỗi ảnh phải xuất hiện đúng một lần. Gợi ý tối đa 5 bài từ thư viện sau:",
        json.dumps(library, ensure_ascii=False),
    ])


def plan_slideshow(
    *, caption: str, image_paths: Sequence[Path], tracks: Sequence[dict[str, Any]], llm: StoryLLM | None,
    requested_seconds: float | None = None, keep_original_order: bool = False, preferred_style: str = "auto",
) -> SlideshowPlan:
    if not image_paths:
        raise ValueError("A slideshow needs at least one image")
    target = suggested_seconds(len(image_paths), requested_seconds)
    if llm is not None:
        images = prompt_images(image_paths) if getattr(llm, "accepts_images", False) else []
        user = _prompt(caption, len(image_paths), tracks, target, keep_original_order, preferred_style, bool(images))
        try:
            raw = (llm.generate_json(SYSTEM_PROMPT, user, RESPONSE_SCHEMA, images=images) if images
                   else llm.generate_json(SYSTEM_PROMPT, user, RESPONSE_SCHEMA))
            return parse_plan(raw, image_count=len(image_paths), tracks=tracks, target_seconds=target,
                              keep_original_order=keep_original_order, source=llm.name)
        except (LLMError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            logger.warning("Photo slideshow planning by %s failed: %s", getattr(llm, "name", "model"), exc)
    return heuristic_plan(caption=caption, image_count=len(image_paths), tracks=tracks, target_seconds=target,
                          keep_original_order=keep_original_order, preferred_style=preferred_style)

