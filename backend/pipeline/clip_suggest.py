"""AI suggestions for the "Cắt clip" feature: the best moments of a translated video.

The model reads the translated subtitles with their times and proposes clips (start, end,
title, reason), optionally steered by what the user wants ("các đoạn đánh nhau"). Its answer
is only a proposal: times are snapped to cue gaps and checked like a hand-made plan, and
anything unusable is dropped instead of failing the whole suggestion.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Protocol, Sequence

from pipeline.clip_planning import (
    FRAME_ORIGINAL,
    MAX_CLIP_SECONDS,
    MAX_TITLE_CHARS,
    MIN_CLIP_SECONDS,
    ClipPlanError,
    ClipSpec,
    cut_points,
    snap_time,
)

MAX_SUGGESTIONS = 10
MAX_WISH_CHARS = 300
# About 30k tokens of transcript; longer videos are sampled evenly so the whole story is seen.
MAX_TRANSCRIPT_CHARS = 120_000


class JSONModel(Protocol):
    name: str

    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> str: ...


SUGGESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["start", "end", "title"],
            },
        },
    },
    "required": ["clips"],
}

SYSTEM_PROMPT = (
    "Bạn là biên tập viên video ngắn cho TikTok, Reels và YouTube Shorts. "
    "Bạn nhận phụ đề tiếng Việt của một video, mỗi dòng có dạng [giây_bắt_đầu-giây_kết_thúc] lời thoại. "
    "Hãy chọn những đoạn hay nhất để cắt thành clip ngắn đứng riêng được: có mở đầu cuốn hút, "
    "một ý hoặc một tình huống trọn vẹn, kết thúc không bị cụt giữa câu. "
    "Mỗi clip bắt đầu ở đầu một câu và kết thúc ở cuối một câu, các clip không chồng lên nhau. "
    "Đặt tiêu đề tiếng Việt ngắn gọn, hấp dẫn (tối đa 60 ký tự) và một lý do ngắn. "
    "Chỉ trả về JSON đúng schema, thời gian tính bằng giây."
)


def _timed_lines(segments: Sequence[dict[str, Any]]) -> list[str]:
    lines = []
    for segment in segments:
        text = " ".join(str(segment.get("text") or "").split())
        if not text or segment.get("skip_subtitle"):
            continue
        try:
            start, end = float(segment["start"]), float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        lines.append(f"[{start:.1f}-{end:.1f}] {text}")
    return lines


def transcript_for_prompt(segments: Sequence[dict[str, Any]], limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Timed subtitle lines; a long video keeps evenly spaced lines so the whole runtime is covered."""
    lines = _timed_lines(segments)
    text = "\n".join(lines)
    if len(text) <= limit or not lines:
        return text
    keep = max(1, int(len(lines) * limit / len(text)))
    step = len(lines) / keep
    return "\n".join(lines[int(index * step)] for index in range(keep))


def build_user_prompt(
    segments: Sequence[dict[str, Any]], duration: float, *, count: int, min_seconds: float,
    max_seconds: float, wish: str = "",
) -> str:
    wish = " ".join(wish.split())[:MAX_WISH_CHARS]
    parts = [
        f"Video dài {duration:.0f} giây.",
        f"Chọn tối đa {count} clip, mỗi clip dài từ {min_seconds:.0f} đến {max_seconds:.0f} giây.",
    ]
    if wish:
        parts.append(f"Người dùng muốn: {wish}. Ưu tiên các đoạn đúng mong muốn này.")
    parts.append("Phụ đề:")
    parts.append(transcript_for_prompt(segments))
    return "\n".join(parts)


def _parse(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            payload = json.loads(text[start:end + 1])
        except ValueError:
            return []
    items = payload.get("clips") if isinstance(payload, dict) else payload
    return [item for item in items or [] if isinstance(item, dict)] if isinstance(items, list) else []


def clean_suggestions(
    raw_clips: Sequence[dict[str, Any]], segments: Sequence[dict[str, Any]], duration: float, *,
    count: int, min_seconds: float, max_seconds: float, frame: str = FRAME_ORIGINAL,
) -> list[ClipSpec]:
    """Snap, bound and de-overlap the model's clips; drop the ones that cannot be cut."""
    points = cut_points(segments, duration)
    kept: list[ClipSpec] = []
    for raw in raw_clips:
        try:
            start, end = float(raw["start"]), float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(start) and math.isfinite(end)):
            continue
        if end > duration + 2 or start < -2:
            continue  # Times the video does not have: the model made them up.
        start = max(0.0, round(snap_time(start, points), 3))
        end = min(round(duration, 3), round(snap_time(end, points), 3))
        length = end - start
        if length < max(MIN_CLIP_SECONDS, min_seconds * 0.6) or length > min(MAX_CLIP_SECONDS, max_seconds * 1.5):
            continue
        if any(start < other.end - 0.05 and other.start < end - 0.05 for other in kept):
            continue
        title = " ".join(str(raw.get("title") or "").split())[:MAX_TITLE_CHARS] or f"Gợi ý {len(kept) + 1}"
        kept.append(ClipSpec(start, end, title, frame))
        if len(kept) >= count:
            break
    return sorted(kept, key=lambda clip: clip.start)


def suggest_clips(
    model: JSONModel, segments: Sequence[dict[str, Any]], duration: float, *, count: int = 5,
    min_seconds: float = 15, max_seconds: float = 60, wish: str = "", frame: str = FRAME_ORIGINAL,
) -> list[ClipSpec]:
    if not _timed_lines(segments):
        raise ClipPlanError("Video này không có phụ đề nên AI không đọc được nội dung để gợi ý")
    if not 1 <= count <= MAX_SUGGESTIONS:
        raise ClipPlanError(f"Số clip gợi ý phải từ 1 đến {MAX_SUGGESTIONS}")
    if not MIN_CLIP_SECONDS <= min_seconds <= max_seconds <= MAX_CLIP_SECONDS:
        raise ClipPlanError(
            f"Độ dài clip gợi ý phải từ {MIN_CLIP_SECONDS:g} giây đến {MAX_CLIP_SECONDS / 60:g} phút"
        )
    raw = model.generate_json(
        SYSTEM_PROMPT,
        build_user_prompt(segments, duration, count=count, min_seconds=min_seconds,
                          max_seconds=max_seconds, wish=wish),
        SUGGESTION_SCHEMA,
    )
    clips = clean_suggestions(_parse(raw), segments, duration, count=count,
                              min_seconds=min_seconds, max_seconds=max_seconds, frame=frame)
    if not clips:
        raise ClipPlanError("AI chưa tìm được đoạn phù hợp; hãy thử đổi mong muốn hoặc độ dài clip")
    return clips
