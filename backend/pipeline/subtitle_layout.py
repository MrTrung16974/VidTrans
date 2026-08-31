from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence


PLACEMENT_MODES = {"replace_original", "above_original", "bottom_safe"}


@dataclass(frozen=True)
class SubtitleLayoutOptions:
    placement_mode: str = "replace_original"
    match_source_size: bool = True
    min_font_size: int = 22
    max_font_size: int = 72
    position_gap: int = 14
    mask_original: bool = True

    @classmethod
    def from_style(cls, style: dict[str, Any]) -> "SubtitleLayoutOptions":
        options = cls(
            placement_mode=str(style.get("placement_mode", "replace_original")),
            match_source_size=bool(style.get("match_source_size", True)),
            min_font_size=int(style.get("min_font_size", 22)),
            max_font_size=int(style.get("max_font_size", 72)),
            position_gap=int(style.get("position_gap", 14)),
            mask_original=bool(style.get("mask_original", True)),
        )
        options.validate()
        return options

    def validate(self) -> None:
        if self.placement_mode not in PLACEMENT_MODES:
            raise ValueError(f"placement_mode must be one of: {', '.join(sorted(PLACEMENT_MODES))}")
        if not 8 <= self.min_font_size <= self.max_font_size <= 200:
            raise ValueError("subtitle font limits must satisfy 8 <= min <= max <= 200")
        if not 0 <= self.position_gap <= 300:
            raise ValueError("position_gap must be between 0 and 300")


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _wrap_for_width(text: str, *, font_size: int, max_width: float) -> str:
    words = " ".join(text.split()).split()
    if not words:
        return ""
    approximate_character_width = max(font_size * 0.54, 1)
    character_limit = max(8, int(max_width / approximate_character_width))
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= character_limit or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) <= 2:
        return "\n".join(lines)
    midpoint = math.ceil(len(words) / 2)
    return " ".join(words[:midpoint]) + "\n" + " ".join(words[midpoint:])


def apply_subtitle_layout(
    segments: Sequence[dict[str, Any]],
    *,
    video_width: int,
    video_height: int,
    style: dict[str, Any],
) -> list[dict[str, Any]]:
    """Attach stable pixel-space layout metadata to translated subtitle cues."""

    if video_width <= 0 or video_height <= 0:
        raise ValueError("video dimensions must be positive")
    options = SubtitleLayoutOptions.from_style(style)
    default_font = int(round(float(style.get("font_size", 36)) * video_height / 720))
    default_font = int(_clamp(default_font, options.min_font_size, options.max_font_size))
    margin_v = int(round(float(style.get("margin_v", 60)) * video_height / 720))
    laid_out: list[dict[str, Any]] = []

    for original in segments:
        segment = dict(original)
        source_bbox = segment.get("source_bbox")
        has_bbox = isinstance(source_bbox, dict) and all(
            key in source_bbox for key in ("x", "y", "width", "height")
        )
        placement_mode = options.placement_mode if has_bbox else "bottom_safe"

        if has_bbox:
            box_x = float(source_bbox["x"]) * video_width
            box_y = float(source_bbox["y"]) * video_height
            box_w = float(source_bbox["width"]) * video_width
            box_h = float(source_bbox["height"]) * video_height
            source_font_height = float(
                segment.get("source_font_height", source_bbox["height"])
            ) * video_height
            matched_font = round(source_font_height * 0.92)
            font_size = matched_font if options.match_source_size else default_font
            font_size = int(_clamp(font_size, options.min_font_size, options.max_font_size))
            max_text_width = min(video_width * 0.92, max(box_w * 1.45, video_width * 0.38))
            render_text = _wrap_for_width(
                str(segment.get("text", "")),
                font_size=font_size,
                max_width=max_text_width,
            )
            line_count = max(1, render_text.count("\n") + 1)
            rendered_height = font_size * line_count * 1.18
            center_x = box_x + box_w / 2
            if placement_mode == "above_original":
                center_y = box_y - options.position_gap - rendered_height / 2
            else:
                center_y = box_y + box_h / 2
            center_x = _clamp(center_x, max_text_width / 2, video_width - max_text_width / 2)
            center_y = _clamp(center_y, rendered_height / 2 + 4, video_height - rendered_height / 2 - 4)
            expand_x = max(8, font_size * 0.3)
            expand_y = max(5, font_size * 0.18)
            mask = {
                "x": round(_clamp(box_x - expand_x, 0, video_width), 2),
                "y": round(_clamp(box_y - expand_y, 0, video_height), 2),
                "width": round(_clamp(box_w + expand_x * 2, 1, video_width), 2),
                "height": round(_clamp(box_h + expand_y * 2, 1, video_height), 2),
            }
            position_source = "ocr"
        else:
            font_size = default_font
            max_text_width = video_width * 0.84
            render_text = _wrap_for_width(
                str(segment.get("text", "")),
                font_size=font_size,
                max_width=max_text_width,
            )
            line_count = max(1, render_text.count("\n") + 1)
            rendered_height = font_size * line_count * 1.18
            center_x = video_width / 2
            center_y = video_height - margin_v - rendered_height / 2
            center_y = _clamp(center_y, rendered_height / 2 + 4, video_height - rendered_height / 2 - 4)
            mask = None
            position_source = "fallback"

        segment["subtitle_layout"] = {
            "x": round(center_x, 2),
            "y": round(center_y, 2),
            "font_size": font_size,
            "render_text": render_text,
            "placement_mode": placement_mode,
            "position_source": position_source,
            "mask_original": bool(
                mask and placement_mode == "replace_original" and options.mask_original
            ),
            "mask": mask,
        }
        laid_out.append(segment)
    return laid_out
