from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from infrastructure.douyin_browser_auth import write_netscape_cookies

logger = logging.getLogger(__name__)


PLATFORM_CONFIG = {
    "youtube": {
        "label": "YouTube",
        "start_url": "https://www.youtube.com/",
        "domains": ("youtube.com", "google.com"),
        "required_cookie_groups": (("SAPISID", "__Secure-3PAPISID"),),
    },
    "facebook": {
        "label": "Facebook",
        "start_url": "https://www.facebook.com/",
        "domains": ("facebook.com",),
        "required_cookie_groups": (("c_user",), ("xs",)),
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _domain_matches(domain: str, allowed_domains: tuple[str, ...]) -> bool:
    normalized = domain.lstrip(".").lower()
    return any(normalized == allowed or normalized.endswith(f".{allowed}") for allowed in allowed_domains)


def filter_platform_cookies(
    cookies: list[dict[str, Any]], allowed_domains: tuple[str, ...]
) -> list[dict[str, Any]]:
    return [
        cookie
        for cookie in cookies
        if _domain_matches(str(cookie.get("domain") or ""), allowed_domains)
    ]


def has_required_cookies(
    cookies: list[dict[str, Any]], required_groups: tuple[tuple[str, ...], ...]
) -> bool:
    names = {
        str(cookie.get("name") or "")
        for cookie in cookies
        if bool(cookie.get("value"))
    }
    return all(any(candidate in names for candidate in group) for group in required_groups)


class SocialBrowserAuthManager:
    """Manage one isolated interactive browser profile without exposing cookie values."""

    def __init__(
        self,
        platform: str,
        cookie_path: Path,
        *,
        cdp_url: str | None = None,
        public_url: str | None = None,
    ) -> None:
        if platform not in PLATFORM_CONFIG:
            raise ValueError(f"Unsupported social browser platform: {platform}")
        config = PLATFORM_CONFIG[platform]
        self.platform = platform
        self.label = str(config["label"])
        self.start_url = str(config["start_url"])
        self.allowed_domains = tuple(config["domains"])
        self.required_cookie_groups = tuple(config["required_cookie_groups"])
        prefix = platform.upper()
        self.cookie_path = Path(cookie_path)
        self.cdp_url = (cdp_url or os.environ.get(
            f"VIDTRANS_{prefix}_BROWSER_CDP_URL", f"http://{platform}-browser:9222"
        )).rstrip("/")
        self.public_url = public_url or os.environ.get(
            f"VIDTRANS_{prefix}_BROWSER_PUBLIC_URL",
            f"http://localhost:{5203 if platform == 'youtube' else 5204}/vnc_lite.html"
            "?autoconnect=true&resize=scale&reconnect=true&path=websockify",
        )
        self._lock = threading.RLock()
        self._last_sync_at: str | None = None

    def _cdp_available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.cdp_url}/json/version", timeout=2) as response:
                return 200 <= int(response.status) < 300
        except (OSError, ValueError, urllib.error.URLError):
            return False

    def _with_browser(self, operation: Callable[[Any], Any]) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Playwright chưa được cài trong backend") from exc

        with urllib.request.urlopen(f"{self.cdp_url}/json/version", timeout=4) as response:
            ws_url = str(json.loads(response.read().decode())["webSocketDebuggerUrl"])
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(ws_url, timeout=8_000)
            if not browser.contexts:
                raise RuntimeError("Chromium chưa tạo browser context")
            return operation(browser)

    @staticmethod
    def _collect_cookies(browser: Any) -> list[dict[str, Any]]:
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for context in browser.contexts:
            for cookie in context.cookies():
                key = (
                    str(cookie.get("domain") or ""),
                    str(cookie.get("path") or "/"),
                    str(cookie.get("name") or ""),
                )
                unique[key] = cookie
        return list(unique.values())

    def _authenticated(self) -> bool:
        return self.cookie_path.is_file() and self.cookie_path.stat().st_size > 80

    def _platform_cookies(self, browser: Any) -> list[dict[str, Any]]:
        return filter_platform_cookies(self._collect_cookies(browser), self.allowed_domains)

    def export_download_cookies(self, target: Path) -> Path:
        with self._lock:
            cookies = self._with_browser(self._platform_cookies)
            write_netscape_cookies(cookies, target)
        return target

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        available = self._cdp_available()
        sync_message: str | None = None
        if refresh and available:
            sync_message = str(self.sync()["message"])
        authenticated = self._authenticated()
        if sync_message:
            message = sync_message
        elif not available:
            message = f"Trình duyệt {self.label} chưa sẵn sàng"
        elif authenticated:
            message = f"Đã đồng bộ phiên đăng nhập {self.label}"
        else:
            message = f"Trình duyệt đã sẵn sàng; hãy đăng nhập {self.label} bên dưới"
        return {
            "platform": self.platform,
            "available": available,
            "authenticated": authenticated,
            "message": message,
            "browser_url": self.public_url if available else None,
            "updated_at": self._last_sync_at,
        }

    def sync(self) -> dict[str, Any]:
        with self._lock:
            try:
                cookies = self._with_browser(self._platform_cookies)
            except Exception as exc:
                logger.warning("Cannot connect to %s browser: %s", self.label, type(exc).__name__)
                return {
                    "platform": self.platform,
                    "available": False,
                    "authenticated": self._authenticated(),
                    "message": f"Không kết nối được Chromium {self.label}. Hãy kiểm tra container {self.platform}-browser.",
                    "browser_url": None,
                    "updated_at": self._last_sync_at,
                }
            if not has_required_cookies(cookies, self.required_cookie_groups):
                self.cookie_path.unlink(missing_ok=True)
                return {
                    "platform": self.platform,
                    "available": True,
                    "authenticated": False,
                    "message": f"Chromium đang mở nhưng {self.label} chưa hoàn tất đăng nhập",
                    "browser_url": self.public_url,
                    "updated_at": self._last_sync_at,
                }
            write_netscape_cookies(cookies, self.cookie_path)
            self._last_sync_at = _utc_now()
            logger.info("Interactive %s session synchronized (%d cookies)", self.label, len(cookies))
            return {
                "platform": self.platform,
                "available": True,
                "authenticated": True,
                "message": f"Đăng nhập thành công; cookie đã sẵn sàng để tải video {self.label}",
                "browser_url": self.public_url,
                "updated_at": self._last_sync_at,
            }

    def reload(self) -> dict[str, Any]:
        def reload_page(browser: Any) -> None:
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            page.bring_to_front()
            page.goto(self.start_url, wait_until="domcontentloaded", timeout=30_000)

        with self._lock:
            try:
                self._with_browser(reload_page)
            except Exception as exc:
                logger.warning("Cannot reload %s browser: %s", self.label, type(exc).__name__)
                raise RuntimeError(f"Không thể tải lại trang {self.label} trong Chromium") from exc
        return self.status()

    def logout(self) -> dict[str, Any]:
        def clear_session(browser: Any) -> None:
            for context in browser.contexts:
                context.clear_cookies()
                for page in context.pages:
                    try:
                        page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
                    except Exception:
                        pass
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(self.start_url, wait_until="domcontentloaded", timeout=30_000)

        with self._lock:
            try:
                self._with_browser(clear_session)
            except Exception as exc:
                logger.warning("Cannot clear %s browser: %s", self.label, type(exc).__name__)
                raise RuntimeError(f"Không thể xóa phiên trong Chromium {self.label}") from exc
            self.cookie_path.unlink(missing_ok=True)
            self._last_sync_at = _utc_now()
        return {
            "platform": self.platform,
            "available": True,
            "authenticated": False,
            "message": f"Đã đăng xuất và xóa cookie {self.label}",
            "browser_url": self.public_url,
            "updated_at": self._last_sync_at,
        }
