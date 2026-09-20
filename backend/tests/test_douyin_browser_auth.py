from __future__ import annotations

import unittest
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from infrastructure.douyin_browser_auth import DouyinBrowserAuthManager, douyin_video_info
from infrastructure.social_video_downloader import SocialVideoDownloadCancelled


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
            if os.name != "nt":
                self.assertEqual(manager.cookie_path.stat().st_mode & 0o777, 0o600)

    def test_download_can_export_guest_cookies_without_reporting_login(self) -> None:
        with TemporaryDirectory() as directory:
            manager = self.manager(directory)
            browser = FakeBrowser([{"domain": ".douyin.com", "name": "ttwid", "value": "guest"}])
            manager._with_browser = lambda operation: operation(browser)
            snapshot = Path(directory) / "job.cookies.txt"
            self.assertEqual(manager.export_download_cookies(snapshot), snapshot)
            self.assertIn("ttwid", snapshot.read_text(encoding="utf-8"))
            self.assertFalse(manager._authenticated())

    def test_video_info_uses_mp4_addresses_and_millisecond_duration(self) -> None:
        detail = {"aweme_id": "7661982102736473384", "desc": "Test", "video": {
            "duration": 12500, "play_addr": {"url_list": [
                "https://v.douyinvod.com/video.mp4", "https://v.douyinvod.com/video.mp4",
                "file:///etc/passwd", "http://127.0.0.1/private", "https://douyinvod.com.evil.test/video",
            ]}}}
        info = douyin_video_info(detail)
        self.assertEqual(info["duration"], 12.5)
        self.assertEqual(len(info["formats"]), 1)
        self.assertIsNone(douyin_video_info({"aweme_id": "1", "video": {}}))

    def test_resolver_selects_requested_id_and_closes_only_its_page(self) -> None:
        with TemporaryDirectory() as directory:
            manager = self.manager(directory)
            browser = FakeBrowser([])
            page = Mock()
            page.url = "https://www.douyin.com/video/123"
            page.locator.return_value.all_text_contents.return_value = []
            page.evaluate.return_value = "Browser User Agent"
            response = Mock()
            response.url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
            response.json.return_value = {"aweme_list": [
                {"aweme_id": item, "desc": item, "video": {"duration": 1000,
                 "play_addr": {"url_list": [f"https://v.douyinvod.com/{item}.mp4"]}}}
                for item in ("999", "123")
            ]}
            page.goto.side_effect = lambda *args, **kwargs: page.on.call_args.args[1](response)
            browser.contexts[0].new_page = lambda: page
            manager._with_browser = lambda operation: operation(browser)
            snapshot = Path(directory) / "download.cookies.txt"
            result = manager.resolve_video("https://v.douyin.com/short/", snapshot)
            self.assertEqual(result["id"], "123")
            self.assertEqual(result["http_headers"]["User-Agent"], "Browser User Agent")
            self.assertTrue(snapshot.is_file())
            page.close.assert_called_once()

    def test_resolver_cancellation_closes_page(self) -> None:
        with TemporaryDirectory() as directory:
            manager = self.manager(directory)
            browser = FakeBrowser([])
            page = Mock()
            browser.contexts[0].new_page = lambda: page
            manager._with_browser = lambda operation: operation(browser)
            with self.assertRaises(SocialVideoDownloadCancelled):
                manager.resolve_video("https://www.douyin.com/video/123", Path(directory) / "cookies.txt", lambda: True)
            page.close.assert_called_once()

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
