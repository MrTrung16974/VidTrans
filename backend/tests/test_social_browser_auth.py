from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from infrastructure.social_browser_auth import (
    SocialBrowserAuthManager,
    facebook_media_url_from_html,
    facebook_video_id,
    filter_platform_cookies,
    has_required_cookies,
)


class SocialBrowserAuthTests(unittest.TestCase):
    def test_filters_cookie_domains_without_leaking_other_sites(self) -> None:
        cookies = [
            {"domain": ".youtube.com", "name": "SAPISID", "value": "yt"},
            {"domain": "accounts.google.com", "name": "SID", "value": "google"},
            {"domain": ".facebook.com", "name": "c_user", "value": "fb"},
        ]
        filtered = filter_platform_cookies(cookies, ("youtube.com", "google.com"))
        self.assertEqual([cookie["value"] for cookie in filtered], ["yt", "google"])

    def test_platform_auth_cookie_contracts(self) -> None:
        self.assertTrue(has_required_cookies(
            [{"name": "__Secure-3PAPISID", "value": "value"}],
            (("SAPISID", "__Secure-3PAPISID"),),
        ))
        self.assertTrue(has_required_cookies(
            [{"name": "c_user", "value": "1"}, {"name": "xs", "value": "2"}],
            (("c_user",), ("xs",)),
        ))
        self.assertFalse(has_required_cookies(
            [{"name": "c_user", "value": "1"}],
            (("c_user",), ("xs",)),
        ))

    def test_sync_writes_only_authenticated_platform_cookies(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "youtube.txt"
            manager = SocialBrowserAuthManager("youtube", path)
            manager._with_browser = Mock(return_value=[
                {"domain": ".youtube.com", "path": "/", "name": "SAPISID", "value": "secret", "secure": True},
            ])
            result = manager.sync()
            self.assertTrue(result["authenticated"])
            self.assertTrue(path.is_file())
            self.assertNotIn("secret", str(result))

    def test_sync_rejects_incomplete_facebook_session(self) -> None:
        with TemporaryDirectory() as directory:
            manager = SocialBrowserAuthManager("facebook", Path(directory) / "facebook.txt")
            manager._with_browser = Mock(return_value=[
                {"domain": ".facebook.com", "name": "c_user", "value": "1"},
            ])
            result = manager.sync()
            self.assertFalse(result["authenticated"])

    def test_download_export_rejects_guest_only_youtube_cookies(self) -> None:
        with TemporaryDirectory() as directory:
            manager = SocialBrowserAuthManager("youtube", Path(directory) / "youtube.txt")
            manager._with_browser = Mock(return_value=[
                {"domain": ".youtube.com", "path": "/", "name": "VISITOR_INFO1_LIVE", "value": "guest"},
            ])
            target = Path(directory) / "snapshot.txt"
            with self.assertRaises(PermissionError):
                manager.export_download_cookies(target)
            self.assertFalse(target.exists())

    def test_facebook_video_ids_from_common_links(self) -> None:
        self.assertEqual(facebook_video_id("https://www.facebook.com/cnn/videos/10155529876156509/"), "10155529876156509")
        self.assertEqual(facebook_video_id("https://www.facebook.com/reel/1195289147628387"), "1195289147628387")
        self.assertEqual(facebook_video_id("https://www.facebook.com/watch/?v=274175099429670"), "274175099429670")
        self.assertIsNone(facebook_video_id("https://fb.watch/abc/"))

    def test_facebook_media_prefers_hd_for_the_requested_video(self) -> None:
        other = '{"id":"999999999","browser_native_hd_url":"https:\\/\\/video.fbcdn.net\\/other.mp4"}'
        filler = "x" * 7_000
        target = (
            '{"id":"1195289147628387","browser_native_sd_url":"https:\\/\\/video.fbcdn.net\\/sd.mp4",'
            '"browser_native_hd_url":"https:\\/\\/video.fbcdn.net\\/hd.mp4?a=1\\u0026b=2"}'
        )
        html = other + filler + target
        self.assertEqual(
            facebook_media_url_from_html(html, "https://www.facebook.com/reel/1195289147628387"),
            "https://video.fbcdn.net/hd.mp4?a=1&b=2",
        )
        self.assertIsNone(facebook_media_url_from_html(other, "https://www.facebook.com/reel/1195289147628387"))

    def test_facebook_title_drops_view_counters(self) -> None:
        from infrastructure.social_browser_auth import _FACEBOOK_TITLE_STATS

        self.assertEqual(_FACEBOOK_TITLE_STATS.sub("", "744K views · 2.3K shares | She survived"), "She survived")
        self.assertEqual(_FACEBOOK_TITLE_STATS.sub("", "Video A | Kênh B"), "Video A | Kênh B")

    def test_facebook_media_ignores_non_facebook_hosts(self) -> None:
        html = '{"browser_native_hd_url":"https:\\/\\/evil.example\\/v.mp4"}'
        self.assertIsNone(facebook_media_url_from_html(html, "https://fb.watch/abc/"))

    def test_reads_user_agent_from_the_cookie_browser_context(self) -> None:
        with TemporaryDirectory() as directory:
            manager = SocialBrowserAuthManager("youtube", Path(directory) / "youtube.txt")
            page = Mock()
            page.evaluate.return_value = "Chromium session UA"
            browser = Mock(contexts=[Mock(pages=[page])])
            manager._with_browser = lambda operation: operation(browser)  # type: ignore[method-assign]

            self.assertEqual(manager.browser_user_agent(), "Chromium session UA")
            page.evaluate.assert_called_once_with("() => navigator.userAgent")

    def test_logout_removes_only_selected_platform_cookies(self) -> None:
        with TemporaryDirectory() as directory:
            manager = SocialBrowserAuthManager("youtube", Path(directory) / "youtube.txt")
            youtube_page = Mock(url="https://www.youtube.com/")
            facebook_page = Mock(url="https://www.facebook.com/")
            context = Mock()
            context.pages = [youtube_page, facebook_page]
            context.cookies.return_value = [
                {"domain": ".youtube.com", "path": "/", "name": "SAPISID", "value": "yt"},
                {"domain": ".facebook.com", "path": "/", "name": "c_user", "value": "fb"},
            ]
            browser = Mock(contexts=[context])
            manager._with_browser = lambda operation: operation(browser)  # type: ignore[method-assign]

            result = manager.logout()

            self.assertFalse(result["authenticated"])
            context.clear_cookies.assert_called_once_with(
                name="SAPISID", domain=".youtube.com", path="/"
            )
            youtube_page.evaluate.assert_called_once()
            facebook_page.evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
