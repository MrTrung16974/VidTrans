"""Navigation menus of the web and mobile clients, configured by admins.

Every screen a menu can open exists in the client code, so the catalog of
``(platform, menu_key)`` pairs is fixed here. What admins change in the
``app_menus`` table is how each entry shows: label, short label, order, on/off,
the lowest role that sees it and the plan feature it needs. The table is seeded
from ``DEFAULT_MENUS``; existing rows are never overwritten.

Menus decide what is shown. Plan features (domain/plan_entitlements.py) decide
what the API allows, so hiding a menu never replaces the server-side check.
"""
from __future__ import annotations

from typing import Any, Mapping

from domain.plan_entitlements import FEATURES, account_features

PLATFORMS = ("web", "mobile")
# Lowest role that sees a menu; owners count as admins.
MENU_ROLES = ("viewer", "editor", "admin")
_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2, "owner": 2}

# The mobile dock shows the first MOBILE_DOCK_SIZE visible entries, so
# low-priority entries (upgrade, account) only fill slots freed by hidden ones.
MOBILE_DOCK_SIZE = 4

# (platform, menu_key, label, short_label, sort_order, required_feature, min_role)
DEFAULT_MENUS: tuple[tuple[str, str, str, str, int, str | None, str], ...] = (
    ("web", "create", "Dịch video", "Dịch", 10, None, "viewer"),
    ("web", "comic", "Truyện → Video", "Truyện", 20, "comic_video", "editor"),
    ("web", "jobs", "Tiến trình", "Tiến trình", 30, None, "viewer"),
    ("web", "tiktok", "Đăng bài mạng xã hội", "Đăng bài", 40, "social_publishing", "editor"),
    ("web", "douyin", "Đăng nhập nguồn", "Nguồn", 50, None, "admin"),
    ("web", "admin", "Quản trị", "Quản trị", 60, None, "admin"),
    ("mobile", "translate", "Dịch", "Dịch", 10, None, "viewer"),
    ("mobile", "comic", "Truyện", "Truyện", 20, "comic_video", "viewer"),
    ("mobile", "jobs", "Tiến trình", "Tiến trình", 30, None, "viewer"),
    ("mobile", "social", "Đăng bài", "Đăng bài", 40, "social_publishing", "viewer"),
    ("mobile", "upgrade", "Nâng cấp", "Nâng cấp", 50, None, "viewer"),
    ("mobile", "profile", "Tài khoản", "Tài khoản", 60, None, "viewer"),
)
MENU_KEYS = frozenset((platform, key) for platform, key, *_ in DEFAULT_MENUS)

# Entries admins cannot hide or open to lower roles, so nobody locks the admin
# screen away from themselves.
LOCKED_MENUS = frozenset({("web", "admin")})


class MenuConfigError(ValueError):
    pass


def role_rank(role: str) -> int:
    return _ROLE_RANK.get(str(role or ""), 0)


def validate_menu_update(platform: str, menu_key: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """Clean an admin edit; raises MenuConfigError with a Vietnamese message."""

    if (platform, menu_key) not in MENU_KEYS:
        raise MenuConfigError("Menu không tồn tại")
    label = str(fields.get("label") or "").strip()
    short_label = str(fields.get("short_label") or "").strip() or label
    if not 1 <= len(label) <= 60:
        raise MenuConfigError("Tên menu phải từ 1 đến 60 ký tự")
    if len(short_label) > 24:
        raise MenuConfigError("Tên ngắn tối đa 24 ký tự")
    try:
        sort_order = int(fields.get("sort_order"))
    except (TypeError, ValueError) as exc:
        raise MenuConfigError("Thứ tự phải là số") from exc
    if isinstance(fields.get("sort_order"), bool) or not 0 <= sort_order <= 999:
        raise MenuConfigError("Thứ tự phải từ 0 đến 999")
    required_feature = fields.get("required_feature") or None
    if required_feature is not None and required_feature not in FEATURES:
        raise MenuConfigError("Tính năng yêu cầu không hợp lệ")
    min_role = str(fields.get("min_role") or "viewer")
    if min_role not in MENU_ROLES:
        raise MenuConfigError("Vai trò tối thiểu không hợp lệ")
    is_enabled = bool(fields.get("is_enabled", True))
    if (platform, menu_key) in LOCKED_MENUS and (not is_enabled or min_role != "admin"):
        raise MenuConfigError("Menu Quản trị luôn bật và chỉ dành cho quản trị viên")
    return {
        "label": label,
        "short_label": short_label,
        "sort_order": sort_order,
        "required_feature": required_feature,
        "min_role": min_role,
        "is_enabled": is_enabled,
    }


def menu_visible(menu: Mapping[str, Any], account: Mapping[str, Any], features: frozenset[str]) -> bool:
    if not menu.get("is_enabled"):
        return False
    if role_rank(str(account.get("role") or "")) < role_rank(str(menu.get("min_role") or "viewer")):
        return False
    feature = menu.get("required_feature")
    return feature is None or feature in features


def visible_menus(menus: list[Mapping[str, Any]], account: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The entries ``account`` sees, in display order."""

    features = account_features(account)
    shown = [menu for menu in menus if menu_visible(menu, account, features)]
    shown.sort(key=lambda menu: (int(menu["sort_order"]), str(menu["menu_key"])))
    return [
        {"key": menu["menu_key"], "label": menu["label"], "short_label": menu["short_label"]}
        for menu in shown
    ]
