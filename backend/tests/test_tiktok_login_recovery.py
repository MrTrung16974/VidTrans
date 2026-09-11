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
