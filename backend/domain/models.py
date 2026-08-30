from __future__ import annotations

from dataclasses import dataclass
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
class ProcessingRequest:
    """Validated request values shared by the HTTP API and future workers."""

    mode: ProcessingMode
    subtitle_source: SubtitleSource
    ocr: OcrOptions
    voice_mode: VoiceRoutingMode
    fallback_voice: VoiceType

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
        )
