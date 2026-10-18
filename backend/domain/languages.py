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


@dataclass(frozen=True)
class VoicePreset:
    key: str
    label_vi: str
    rate_percent: int
    pitch_hz: int


VOICE_PRESETS: dict[str, VoicePreset] = {
    "natural": VoicePreset("natural", "Tự nhiên", 0, 0),
    # Edge voices already carry a trained speaking identity. Large pitch shifts
    # make Vietnamese vowels sound metallic, so styles deliberately stay subtle.
    "warm": VoicePreset("warm", "Ấm áp", -3, -4),
    "bright": VoicePreset("bright", "Tươi sáng", 2, 4),
    "deep": VoicePreset("deep", "Trầm", -4, -7),
    "energetic": VoicePreset("energetic", "Năng động", 5, 2),
    "gentle": VoicePreset("gentle", "Nhẹ nhàng", -5, -2),
    "storyteller": VoicePreset("storyteller", "Kể chuyện", -5, -3),
    "news": VoicePreset("news", "Bản tin", 1, -1),
    "commercial": VoicePreset("commercial", "Quảng cáo", 4, 3),
    "youthful": VoicePreset("youthful", "Trẻ trung", 3, 5),
    "authoritative": VoicePreset("authoritative", "Uy tín", -3, -6),
    "calm": VoicePreset("calm", "Điềm tĩnh", -6, -3),
    "cheerful": VoicePreset("cheerful", "Vui vẻ", 5, 5),
}


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


def voice_preset(key: str) -> VoicePreset:
    normalized = (key or "natural").strip().lower()
    if normalized not in VOICE_PRESETS:
        raise ValueError(f"voice_style must be one of: {', '.join(VOICE_PRESETS)}")
    return VOICE_PRESETS[normalized]


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
