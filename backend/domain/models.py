from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum, StrEnum


class ProcessingMode(IntEnum):
    SUBTITLES = 1
    DUBBED = 2
    DUBBED_WITH_MUSIC = 3


class SubtitleSource(StrEnum):
    AUTO = "auto"
    BURNED = "burned"
    SPEECH = "speech"


class VoiceRoutingMode(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


class VoiceType(StrEnum):
    FEMALE = "female"
    MALE = "male"


@dataclass(frozen=True)
class OcrOptions:
    sample_fps: float
    roi_top: float
    roi_bottom: float


@dataclass(frozen=True)
class TikTokOptions:
    enabled: bool = True
    max_summary_chars: int = 350
    hashtag_count: int = 6
    auto_publish: bool = False
    privacy_level: str = "SELF_ONLY"
    publish_at: str | None = None


@dataclass(frozen=True)
class ProcessingRequest:
    """Validated request values shared by the HTTP API and future workers."""

    mode: ProcessingMode
    subtitle_source: SubtitleSource
    ocr: OcrOptions
    voice_mode: VoiceRoutingMode
    fallback_voice: VoiceType
    tiktok: TikTokOptions

    @classmethod
    def from_form(
        cls,
        *,
        mode: int,
        subtitle_source: str,
        ocr_sample_fps: float,
        ocr_roi_top: float,
        ocr_roi_bottom: float,
        voice_mode: str,
        voice_type: str,
        generate_tiktok_post: bool = True,
        tiktok_max_summary_chars: int = 350,
        tiktok_hashtag_count: int = 6,
        auto_publish_tiktok: bool = False,
        tiktok_privacy_level: str = "SELF_ONLY",
        tiktok_publish_at: str | None = None,
    ) -> "ProcessingRequest":
        try:
            resolved_mode = ProcessingMode(mode)
        except ValueError as exc:
            raise ValueError("mode must be 1, 2, or 3") from exc
        try:
            resolved_source = SubtitleSource(subtitle_source)
        except ValueError as exc:
            raise ValueError("subtitle_source must be auto, burned, or speech") from exc
        try:
            resolved_voice_mode = VoiceRoutingMode(voice_mode)
        except ValueError as exc:
            raise ValueError("voice_mode must be auto or manual") from exc
        try:
            resolved_voice_type = VoiceType(voice_type)
        except ValueError as exc:
            raise ValueError("voice_type must be female or male") from exc
        if not 80 <= tiktok_max_summary_chars <= 1500:
            raise ValueError("tiktok_max_summary_chars must be between 80 and 1500")
        if not 0 <= tiktok_hashtag_count <= 12:
            raise ValueError("tiktok_hashtag_count must be between 0 and 12")
        allowed_privacy_levels = {
            "PUBLIC_TO_EVERYONE",
            "MUTUAL_FOLLOW_FRIENDS",
            "FOLLOWER_OF_CREATOR",
            "SELF_ONLY",
        }
        if tiktok_privacy_level not in allowed_privacy_levels:
            raise ValueError("tiktok_privacy_level is invalid")
        if auto_publish_tiktok and not generate_tiktok_post:
            raise ValueError("generate_tiktok_post must be enabled when auto_publish_tiktok is enabled")
        normalized_publish_at = None
        if tiktok_publish_at and tiktok_publish_at.strip():
            if not auto_publish_tiktok:
                raise ValueError("auto_publish_tiktok must be enabled when tiktok_publish_at is set")
            try:
                parsed_publish_at = datetime.fromisoformat(
                    tiktok_publish_at.strip().replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ValueError("tiktok_publish_at must be a valid ISO-8601 datetime") from exc
            if parsed_publish_at.tzinfo is None:
                raise ValueError("tiktok_publish_at must include a timezone")
            parsed_publish_at = parsed_publish_at.astimezone(timezone.utc)
            now = datetime.now(timezone.utc)
            if parsed_publish_at <= now + timedelta(seconds=30):
                raise ValueError("tiktok_publish_at must be at least 30 seconds in the future")
            if parsed_publish_at > now + timedelta(days=365):
                raise ValueError("tiktok_publish_at cannot be more than 365 days in the future")
            normalized_publish_at = parsed_publish_at.isoformat()
        return cls(
            mode=resolved_mode,
            subtitle_source=resolved_source,
            ocr=OcrOptions(
                sample_fps=ocr_sample_fps,
                roi_top=ocr_roi_top,
                roi_bottom=ocr_roi_bottom,
            ),
            voice_mode=resolved_voice_mode,
            fallback_voice=resolved_voice_type,
            tiktok=TikTokOptions(
                enabled=generate_tiktok_post,
                max_summary_chars=tiktok_max_summary_chars,
                hashtag_count=tiktok_hashtag_count,
                auto_publish=auto_publish_tiktok,
                privacy_level=tiktok_privacy_level,
                publish_at=normalized_publish_at,
            ),
        )
