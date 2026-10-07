import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import MagicMock, patch
from infrastructure.tiktok_browser import TikTokBrowserManager, TikTokBrowserError


class LoginRecoveryTests(unittest.TestCase):
    def test_opens_login_chooser_and_refreshes_session(self):
        with TemporaryDirectory() as directory:
            manager = TikTokBrowserManager(Path(directory))
            page = MagicMock()
            with patch.object(manager, '_page', return_value=page), patch.object(manager, '_with_browser', side_effect=lambda operation: operation(object())), patch.object(manager, 'status', return_value={}) as status:
                manager.restart_login()
                page.goto.assert_called_once_with('https://www.tiktok.com/login', wait_until='domcontentloaded', timeout=30_000)
                status.assert_called_once_with(refresh=True)
                self.assertFalse(manager._browser_lock.locked())

    def test_active_draft_blocks_navigation(self):
        with TemporaryDirectory() as directory:
            manager = TikTokBrowserManager(Path(directory))
            with patch.object(manager, 'active_attempt', return_value={'state': 'awaiting_review'}), patch.object(manager, '_with_browser') as browser:
                with self.assertRaises(TikTokBrowserError):
                    manager.restart_login()
                browser.assert_not_called()

    def test_rate_limit_blocks_email_but_allows_qr_recovery(self):
        with TemporaryDirectory() as directory:
            manager = TikTokBrowserManager(Path(directory))
            page = MagicMock()
            page.frames = [page]
            page.locator.return_value.inner_text.return_value = "Maximum number of attempts reached. Try again later."
            browser = MagicMock()
            browser.contexts = [MagicMock()]
            with patch.object(manager, '_page', return_value=page), patch.object(manager, '_with_browser', side_effect=lambda operation: operation(browser)), patch.object(manager, 'status', return_value={}):
                manager.restart_login('qr')
                browser.contexts[0].clear_cookies.assert_called_once_with()
                self.assertEqual(
                    [call.args[0] for call in page.goto.call_args_list],
                    ['about:blank', 'https://www.tiktok.com/login?loginType=qrCode'],
                )
                cdp = browser.contexts[0].new_cdp_session.return_value
                cdp.send.assert_any_call('Network.clearBrowserCookies')
                cdp.send.assert_any_call('Network.clearBrowserCache')
                cdp.send.assert_any_call(
                    'Storage.clearDataForOrigin',
                    {'origin': 'https://www.tiktok.com', 'storageTypes': 'all'},
                )
                page.reset_mock()
                page.frames = [page]
                page.locator.return_value.inner_text.return_value = "Maximum number of attempts reached. Try again later."
                with self.assertRaisesRegex(TikTokBrowserError, "giới hạn"):
                    manager.login_with_email('test@example.com', 'not-a-real-password')
                page.goto.assert_not_called()
                self.assertFalse(manager._browser_lock.locked())

    def test_read_only_check_clears_limit_when_notice_disappears(self):
        with TemporaryDirectory() as directory:
            manager = TikTokBrowserManager(Path(directory))
            browser = MagicMock()
            page = MagicMock()
            page.url = 'https://www.tiktok.com/login'
            page.frames = [page]
            browser.contexts[0].pages = [page]
            browser.contexts[0].cookies.return_value = []
            page.locator.return_value.inner_text.return_value = 'Too many attempts'
            manager._read_session(browser)
            self.assertTrue(manager._login_limited)
            page.locator.return_value.inner_text.return_value = 'Enter 6-digit code'
            manager._read_session(browser)
            self.assertFalse(manager._login_limited)
            page.goto.assert_not_called()
