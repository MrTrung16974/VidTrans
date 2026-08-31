from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.douyin_qr_auth import (
    DouyinQRAuthManager,
    has_authenticated_cookie,
    write_netscape_cookies,
)


class DouyinQRAuthTests(unittest.TestCase):
    def test_detects_authenticated_browser_cookie(self) -> None:
        self.assertTrue(has_authenticated_cookie([{"name": "sessionid", "value": "abc"}]))
        self.assertFalse(has_authenticated_cookie([{"name": "ttwid", "value": "abc"}]))

    def test_writes_yt_dlp_compatible_netscape_file(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory) / "cookies.txt"
            write_netscape_cookies(
                [{
                    "domain": ".douyin.com",
                    "path": "/",
                    "secure": True,
                    "expires": 2_000_000_000,
                    "name": "sessionid",
                    "value": "secret",
                }],
                target,
            )
            payload = target.read_text(encoding="utf-8")
            self.assertTrue(payload.startswith("# Netscape HTTP Cookie File\n"))
            self.assertIn(".douyin.com\tTRUE\t/\tTRUE\t2000000000\tsessionid\tsecret", payload)

    def test_auth_status_never_exposes_cookie_contents(self) -> None:
        with TemporaryDirectory() as directory:
            manager = DouyinQRAuthManager(Path(directory))
            manager.cookie_path.write_text("# Netscape HTTP Cookie File\n" + "x" * 100, encoding="utf-8")
            status = manager.auth_status()
            self.assertTrue(status["authenticated"])
            self.assertNotIn("cookie", status)


if __name__ == "__main__":
    unittest.main()
