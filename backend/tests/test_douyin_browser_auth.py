from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.douyin_browser_auth import DouyinBrowserAuthManager


class FakeContext:
    def __init__(self, cookies: list[dict[str, object]]) -> None:
        self._cookies = cookies

    def cookies(self) -> list[dict[str, object]]:
        return self._cookies


class FakeBrowser:
    def __init__(self, cookies: list[dict[str, object]]) -> None:
        self.contexts = [FakeContext(cookies)]


class DouyinBrowserAuthTests(unittest.TestCase):
    def manager(self, directory: str) -> DouyinBrowserAuthManager:
        return DouyinBrowserAuthManager(
            Path(directory) / "douyin.cookies.txt",
            cdp_url="http://browser:9222",
            public_url="/douyin-browser/vnc_lite.html",
        )

    def test_sync_exports_authenticated_cookie_without_returning_secret(self) -> None:
        with TemporaryDirectory() as directory:
            manager = self.manager(directory)
            browser = FakeBrowser([
                {
                    "domain": ".douyin.com",
                    "path": "/",
                    "name": "sessionid",
                    "value": "top-secret-value",
                    "secure": True,
                    "expires": 1_900_000_000,
                }
            ])
            manager._with_browser = lambda operation: operation(browser)  # type: ignore[method-assign]

            result = manager.sync()

            self.assertTrue(result["authenticated"])
            self.assertNotIn("top-secret-value", str(result))
            self.assertIn("top-secret-value", manager.cookie_path.read_text(encoding="utf-8"))
            self.assertEqual(manager.cookie_path.stat().st_mode & 0o777, 0o600)

    def test_sync_removes_stale_cookie_when_browser_is_logged_out(self) -> None:
        with TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.cookie_path.write_text("stale" * 30, encoding="utf-8")
            browser = FakeBrowser([
                {"domain": ".douyin.com", "path": "/", "name": "csrf_session_id", "value": "not-auth"}
            ])
            manager._with_browser = lambda operation: operation(browser)  # type: ignore[method-assign]

            result = manager.sync()

            self.assertFalse(result["authenticated"])
            self.assertFalse(manager.cookie_path.exists())

    def test_status_only_exposes_browser_url_when_cdp_is_available(self) -> None:
        with TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager._cdp_available = lambda: False  # type: ignore[method-assign]
            unavailable = manager.status()
            self.assertFalse(unavailable["available"])
            self.assertIsNone(unavailable["browser_url"])

            manager._cdp_available = lambda: True  # type: ignore[method-assign]
            available = manager.status()
            self.assertTrue(available["available"])
            self.assertEqual(available["browser_url"], "/douyin-browser/vnc_lite.html")


if __name__ == "__main__":
    unittest.main()
