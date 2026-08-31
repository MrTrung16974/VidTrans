from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.douyin_qr_auth import (
    DouyinQRAuthManager,
    QRLoginSession,
    has_authenticated_cookie,
    looks_like_sms_challenge,
    mask_phone,
    normalize_otp,
    normalize_phone,
    write_netscape_cookies,
)


class DouyinQRAuthTests(unittest.TestCase):
    def test_otp_submit_uses_visible_text_control_when_not_a_native_button(self) -> None:
        class Candidate:
            def __init__(self, visible: bool, clicks: list[str]) -> None:
                self.visible = visible
                self.clicks = clicks

            def is_visible(self) -> bool:
                return self.visible

            def click(self, **_kwargs: object) -> None:
                self.clicks.append("clicked")

        class Locator:
            def __init__(self, candidates: list[Candidate]) -> None:
                self.candidates = candidates

            def count(self) -> int:
                return len(self.candidates)

            def nth(self, index: int) -> Candidate:
                return self.candidates[index]

            def filter(self, **_kwargs: object) -> "Locator":
                return self

        class Page:
            def __init__(self, text_control: Locator) -> None:
                self.text_control = text_control

            def get_by_role(self, *_args: object, **_kwargs: object) -> Locator:
                return Locator([])

            def get_by_text(self, *_args: object, **_kwargs: object) -> Locator:
                return self.text_control

            def locator(self, *_args: object, **_kwargs: object) -> Locator:
                return Locator([])

        class OtpInput:
            def press(self, _key: str) -> None:
                raise AssertionError("Enter fallback should not be needed")

        clicks: list[str] = []
        DouyinQRAuthManager._submit_otp_form(Page(Locator([Candidate(True, clicks)])), OtpInput())
        self.assertEqual(clicks, ["clicked"])

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

    def test_normalizes_douyin_phone_and_otp(self) -> None:
        self.assertEqual(normalize_phone("86", "138 0013 8000"), ("+86", "13800138000"))
        self.assertEqual(normalize_phone("+84", "912-345-678"), ("+84", "912345678"))
        self.assertEqual(normalize_otp(" 12 34 56 "), "123456")
        self.assertEqual(mask_phone("+86", "13800138000"), "+86 •••••••8000")

    def test_rejects_invalid_phone_and_otp(self) -> None:
        for country_code, phone in [("+86", "123"), ("+86", "12800138000"), ("+0", "123456")]:
            with self.subTest(country_code=country_code, phone=phone):
                with self.assertRaises(ValueError):
                    normalize_phone(country_code, phone)
        for otp in ["", "123", "12ab56", "123456789"]:
            with self.subTest(otp=otp):
                with self.assertRaises(ValueError):
                    normalize_otp(otp)

    def test_detects_sms_challenge_after_qr_scan(self) -> None:
        self.assertTrue(looks_like_sms_challenge("接收短信验证码\n短信已发送至 +84*******00"))
        self.assertTrue(looks_like_sms_challenge("24s后重新发送"))
        self.assertFalse(looks_like_sms_challenge("扫码登录 验证码登录 密码登录"))

    def test_session_snapshot_never_exposes_phone_or_otp(self) -> None:
        with TemporaryDirectory() as directory:
            manager = DouyinQRAuthManager(Path(directory))
            session = QRLoginSession(
                session_id="test-session",
                image_path=Path(directory) / "login.png",
                status="waiting_scan",
            )
            manager._session = session
            session.otp_attempts = 4

            phone_snapshot = manager.submit_phone("test-session", "+86", "13800138000")
            self.assertEqual(phone_snapshot["phone_masked"], "+86 •••••••8000")
            self.assertNotIn("13800138000", repr(phone_snapshot))
            self.assertEqual(session.otp_attempts, 0)
            command, payload = session.commands.get_nowait()
            self.assertEqual(command, "phone")
            payload.clear()

            session.status = "waiting_otp"
            otp_snapshot = manager.submit_otp("test-session", "123456")
            self.assertNotIn("123456", repr(otp_snapshot))
            self.assertEqual(otp_snapshot["status"], "verifying_otp")


if __name__ == "__main__":
    unittest.main()
