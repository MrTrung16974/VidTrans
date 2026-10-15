from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from infrastructure.social_browser_auth import (
    SocialBrowserAuthManager,
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
