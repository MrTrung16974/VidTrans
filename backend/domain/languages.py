from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageProfile:
    code: str
    label_vi: str
    whisper_code: str
    google_code: str
    mymemory_code: str
    gtts_code: str
    edge_female: str
    edge_male: str
    font_family: str = "Noto Sans"
    ocr_supported: bool = False

    def edge_voice(self, voice_type: str) -> str:
        return self.edge_male if voice_type == "male" else self.edge_female


LANGUAGES: dict[str, LanguageProfile] = {
    "vi": LanguageProfile("vi", "Tiếng Việt", "vi", "vi", "vi", "vi", "vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"),
    "en": LanguageProfile("en", "Tiếng Anh", "en", "en", "en", "en", "en-US-JennyNeural", "en-US-GuyNeural"),
    "zh-CN": LanguageProfile("zh-CN", "Tiếng Trung", "zh", "zh-CN", "zh-CN", "zh-CN", "zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "Noto Sans CJK SC", True),
    "ja": LanguageProfile("ja", "Tiếng Nhật", "ja", "ja", "ja", "ja", "ja-JP-NanamiNeural", "ja-JP-KeitaNeural", "Noto Sans CJK SC"),
    "ko": LanguageProfile("ko", "Tiếng Hàn", "ko", "ko", "ko", "ko", "ko-KR-SunHiNeural", "ko-KR-InJoonNeural", "Noto Sans CJK SC"),
    "th": LanguageProfile("th", "Tiếng Thái", "th", "th", "th", "th", "th-TH-PremwadeeNeural", "th-TH-NiwatNeural", "Noto Sans Thai"),
    "id": LanguageProfile("id", "Tiếng Indonesia", "id", "id", "id", "id", "id-ID-GadisNeural", "id-ID-ArdiNeural"),
    "es": LanguageProfile("es", "Tiếng Tây Ban Nha", "es", "es", "es", "es", "es-ES-ElviraNeural", "es-ES-AlvaroNeural"),
}

_ALIASES = {
    "zh": "zh-CN", "zh-cn": "zh-CN", "chinese": "zh-CN",
    "jp": "ja", "kr": "ko", "in": "id",
}


def normalize_language(code: str, *, allow_auto: bool = False) -> str:
    value = (code or "").strip()
    if allow_auto and value.lower() == "auto":
        return "auto"
    canonical = _ALIASES.get(value.lower(), value)
    if canonical not in LANGUAGES:
        allowed = ", ".join((["auto"] if allow_auto else []) + list(LANGUAGES))
        raise ValueError(f"language must be one of: {allowed}")
    return canonical


def language_profile(code: str) -> LanguageProfile:
    return LANGUAGES[normalize_language(code)]


def validate_language_pair(source_language: str, target_language: str) -> tuple[str, str]:
    source = normalize_language(source_language, allow_auto=True)
    target = normalize_language(target_language)
    if source != "auto" and source == target:
        raise ValueError("source_language and target_language must be different")
    return source, target


def languages_from_config(config: dict) -> tuple[str, str]:
    """Read a saved job while preserving the legacy Chinese-to-Vietnamese default."""
    return validate_language_pair(
        str(config.get("source_language", "zh-CN")),
        str(config.get("target_language", "vi")),
    )
