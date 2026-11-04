"""What each billing plan may use.

The rules live in the database (``plans``, ``plan_features`` and
``plan_entitlements``, see infrastructure/plan_store.py), seeded from the
defaults below: one explicit allow/deny row per plan and feature, trial and free
included. ``configure_entitlement_source`` plugs that store in at start-up;
without it (unit tests, scripts) the defaults apply.

Trial and free get video translation with the free/local engines (Google
Translate, TranslateGemma, Qwen3, Edge TTS, CIT Voice Studio, Azure Speech) and
screenshot/image translation.
Comic videos, social publishing and the per-request AI (Gemini, ElevenLabs,
Veo) need Creator, Pro or Enterprise. Owners and admins are never limited: they
run the service and test every provider.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol

UNRESTRICTED_ROLES = frozenset({"owner", "admin"})
DEFAULT_PLAN = "free"

# feature_key -> (label shown to users, description for the plan_features table)
FEATURES: dict[str, tuple[str, str]] = {
    "image_translation": (
        "Dịch ảnh chụp màn hình",
        "Nhận diện chữ trong ảnh và thay bằng bản dịch đúng vị trí (OCR trên máy, dịch offline hoặc qua máy chủ)",
    ),
    "comic_video": ("Tạo video truyện", "Tạo, sửa và render truyện thành video"),
    "novel_import": (
        "Nhập chương truyện từ link",
        "Tải chương đọc được (miễn phí hoặc đã mua) từ Qidian, Jinjiang vào truyện và tự chia tập",
    ),
    "social_publishing": (
        "Đăng bài mạng xã hội",
        "Kết nối tài khoản, đăng và hẹn giờ đăng lên TikTok, YouTube, Facebook, Instagram, Threads",
    ),
    "ai_translation_gemini": ("AI dịch phụ đề Gemini AI", "Dịch phụ đề bằng Gemini (tính phí theo lượt)"),
    "ai_tts_elevenlabs": ("AI lồng tiếng ElevenLabs", "Lồng tiếng bằng ElevenLabs (tính phí theo lượt)"),
    "ai_comic_script_gemini": ("AI viết kịch bản Gemini", "Viết kịch bản truyện bằng Gemini"),
    "ai_comic_image_gemini": ("AI vẽ ảnh Gemini Image", "Vẽ minh họa truyện bằng Gemini Image"),
    "ai_comic_animation_veo": ("AI tạo chuyển động Google Veo", "Tạo chuyển động cho cảnh truyện bằng Veo"),
}

# plan_key -> plan row defaults. Prices in VND (None = quoted per customer),
# minutes per month (None = unlimited).
DEFAULT_PLANS: dict[str, dict[str, Any]] = {
    # Auto-generated guest accounts: a tiny taste of the pipeline, never sold.
    "trial": {"name": "Dùng thử", "price_vnd": 0, "monthly_minutes": 5, "is_paid": False, "sort_order": 0},
    "free": {"name": "Free", "price_vnd": 0, "monthly_minutes": 30, "is_paid": False, "sort_order": 1},
    "creator": {"name": "Creator", "price_vnd": 199_000, "monthly_minutes": 300, "is_paid": True, "sort_order": 2},
    "pro": {"name": "Pro", "price_vnd": 499_000, "monthly_minutes": 1_000, "is_paid": True, "sort_order": 3},
    "enterprise": {"name": "Enterprise", "price_vnd": None, "monthly_minutes": None, "is_paid": True,
                   "sort_order": 4},
}

_PAID_FEATURES = frozenset(FEATURES)
# Features every plan gets out of the box; admins may still switch them off per plan.
_BASIC_FEATURES = frozenset({"image_translation"})
# plan_key -> enabled features; every feature not listed is an explicit deny row.
DEFAULT_PLAN_FEATURES: dict[str, frozenset[str]] = {
    "trial": _BASIC_FEATURES,
    "free": _BASIC_FEATURES,
    "creator": _PAID_FEATURES,
    "pro": _PAID_FEATURES,
    "enterprise": _PAID_FEATURES,
}

# (provider kind, provider key) -> feature that unlocks it. Providers not listed are free.
PROVIDER_FEATURES: dict[tuple[str, str], str] = {
    ("translation", "gemini"): "ai_translation_gemini",
    ("tts", "elevenlabs"): "ai_tts_elevenlabs",
    ("comic_llm", "gemini"): "ai_comic_script_gemini",
    ("comic_image", "gemini"): "ai_comic_image_gemini",
    ("comic_animation", "veo"): "ai_comic_animation_veo",
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


class EntitlementSource(Protocol):
    def features_for(self, plan_key: str) -> frozenset[str]: ...


class _DefaultSource:
    def features_for(self, plan_key: str) -> frozenset[str]:
        return DEFAULT_PLAN_FEATURES.get(plan_key, frozenset())


_source: EntitlementSource = _DefaultSource()


def configure_entitlement_source(source: EntitlementSource | None) -> None:
    """Read entitlements from ``source`` (the plan store); None restores the defaults."""

    global _source
    _source = source or _DefaultSource()


def account_plan(account: Mapping[str, Any]) -> str:
    return str(account.get("plan") or DEFAULT_PLAN).strip().lower() or DEFAULT_PLAN


def account_features(account: Mapping[str, Any]) -> frozenset[str]:
    """Every feature the account may use; unknown plans get nothing."""

    if str(account.get("role") or "") in UNRESTRICTED_ROLES:
        return frozenset(FEATURES)
    return _source.features_for(account_plan(account)) & frozenset(FEATURES)


def feature_allowed(account: Mapping[str, Any], feature: str) -> bool:
    if feature not in FEATURES:
        raise KeyError(f"unknown plan feature: {feature}")
    return feature in account_features(account)


def ensure_feature_allowed(account: Mapping[str, Any], feature: str) -> None:
    if not feature_allowed(account, feature):
        raise PlanEntitlementError(
            f"{FEATURES[feature][0]} chỉ dành cho {UPGRADE_HINT}. Hãy nâng cấp gói để sử dụng."
        )


def provider_allowed(account: Mapping[str, Any], kind: str, provider: str) -> bool:
    feature = PROVIDER_FEATURES.get((kind, provider))
    return feature is None or feature_allowed(account, feature)


def ensure_provider_allowed(account: Mapping[str, Any], kind: str, provider: str, label: str | None = None) -> None:
    if not provider_allowed(account, kind, provider):
        name = label or provider
        raise PlanEntitlementError(
            f"{KIND_LABELS.get(kind, 'Nhà cung cấp')} {name} chỉ dành cho {UPGRADE_HINT}. "
            "Hãy nâng cấp gói hoặc chọn nhà cung cấp miễn phí."
        )


def annotate_providers(account: Mapping[str, Any], kind: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add ``allowed``/``requires_paid_plan`` so clients can disable locked options."""

    return [
        {
            **item,
            "requires_paid_plan": (kind, item["key"]) in PROVIDER_FEATURES,
            "allowed": provider_allowed(account, kind, item["key"]),
        }
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
