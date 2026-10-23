"""Which AI providers each billing plan may use.

Free accounts get the free/local engines (Google Translate, TranslateGemma, Qwen3,
Edge TTS, CIT Voice Studio, Azure Speech). Gemini and ElevenLabs cost per request,
so they need a paid plan (Creator, Pro or Enterprise). Owners and admins are not
limited: they run the service and test every provider.
"""
from __future__ import annotations

from typing import Any, Mapping

PAID_PLANS = frozenset({"creator", "pro", "enterprise"})
UNRESTRICTED_ROLES = frozenset({"owner", "admin"})

# kind -> provider keys that require a paid plan
PAID_ONLY_PROVIDERS: dict[str, frozenset[str]] = {
    "translation": frozenset({"gemini"}),
    "tts": frozenset({"elevenlabs"}),
    "comic_llm": frozenset({"gemini"}),
    "comic_image": frozenset({"gemini"}),
    "comic_animation": frozenset({"veo"}),
}

KIND_LABELS = {
    "translation": "AI dịch phụ đề",
    "tts": "AI lồng tiếng",
    "comic_llm": "AI viết kịch bản",
    "comic_image": "AI vẽ ảnh",
    "comic_animation": "AI tạo chuyển động",
}

UPGRADE_HINT = "gói Creator, Pro hoặc Enterprise"


class PlanEntitlementError(PermissionError):
    pass


def has_paid_features(account: Mapping[str, Any]) -> bool:
    if str(account.get("role") or "") in UNRESTRICTED_ROLES:
        return True
    return str(account.get("plan") or "free").strip().lower() in PAID_PLANS


def provider_allowed(account: Mapping[str, Any], kind: str, provider: str) -> bool:
    if provider not in PAID_ONLY_PROVIDERS.get(kind, frozenset()):
        return True
    return has_paid_features(account)


def ensure_provider_allowed(account: Mapping[str, Any], kind: str, provider: str, label: str | None = None) -> None:
    if not provider_allowed(account, kind, provider):
        name = label or provider
        raise PlanEntitlementError(
            f"{KIND_LABELS.get(kind, 'Nhà cung cấp')} {name} chỉ dành cho {UPGRADE_HINT}. "
            "Hãy nâng cấp gói hoặc chọn nhà cung cấp miễn phí."
        )


def annotate_providers(account: Mapping[str, Any], kind: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add ``allowed``/``requires_paid_plan`` so clients can disable locked options."""

    locked = PAID_ONLY_PROVIDERS.get(kind, frozenset())
    return [
        {**item, "requires_paid_plan": item["key"] in locked, "allowed": provider_allowed(account, kind, item["key"])}
        for item in items
    ]


def ensure_video_providers_allowed(
    account: Mapping[str, Any], *, translation_provider: str, tts_provider: str, dubbing: bool
) -> None:
    ensure_provider_allowed(account, "translation", translation_provider, "Gemini AI")
    if dubbing:
        ensure_provider_allowed(account, "tts", tts_provider, "ElevenLabs")


def ensure_comic_settings_allowed(account: Mapping[str, Any], settings: Mapping[str, Any]) -> None:
    ensure_provider_allowed(account, "comic_llm", str(settings.get("llm_provider") or ""), "Gemini")
    ensure_provider_allowed(account, "comic_image", str(settings.get("image_provider") or ""), "Gemini Image")
    if settings.get("video_mode") == "animated":
        ensure_provider_allowed(account, "comic_animation", str(settings.get("animation_provider") or ""), "Google Veo")
