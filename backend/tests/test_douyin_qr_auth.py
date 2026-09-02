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
    def test_clicks_submit_inside_second_verification_layer(self) -> None:
        class Candidate:
            def __init__(self, *, visible: bool = True) -> None:
                self.visible = visible
                self.clicked = False

            def is_visible(self) -> bool:
                return self.visible

            def get_attribute(self, _name: str) -> None:
                return None

            def click(self, **_kwargs: object) -> None:
                self.clicked = True

            def get_by_role(self, *_args: object, **_kwargs: object) -> "Locator":
                return Locator([])

            def get_by_text(self, label: str, **_kwargs: object) -> "Locator":
                return Locator([submit] if label == "验证" else [])

            def locator(self, *_args: object, **_kwargs: object) -> "Locator":
                return Locator([])

        class Locator:
            def __init__(self, candidates: list[Candidate]) -> None:
                self.candidates = candidates

            @property
            def last(self) -> Candidate:
                return self.candidates[-1]

            def count(self) -> int:
                return len(self.candidates)

            def nth(self, index: int) -> Candidate:
                return self.candidates[index]

            def filter(self, **_kwargs: object) -> "Locator":
                return self

        class Page:
            def locator(self, selector: str) -> Locator:
                return Locator([layer]) if selector == "#uc-second-verify" else Locator([])

        submit = Candidate()
        layer = Candidate()
        self.assertTrue(DouyinQRAuthManager._click_second_verify_otp_submit(Page()))
        self.assertTrue(submit.clicked)

    def test_clicks_current_direct_douyin_otp_control(self) -> None:
        class Candidate:
            def __init__(self) -> None:
                self.clicked = False

            def is_visible(self) -> bool:
                return True

            def get_attribute(self, _name: str) -> None:
                return None

            def click(self, **_kwargs: object) -> None:
                self.clicked = True

        class Locator:
            def __init__(self, candidate: Candidate) -> None:
                self.last = candidate

            def count(self) -> int:
                return 1

        class Page:
            def __init__(self, candidate: Candidate) -> None:
                self.candidate = candidate

            def locator(self, _selector: str) -> Locator:
                return Locator(self.candidate)

        candidate = Candidate()
        self.assertTrue(DouyinQRAuthManager._click_current_douyin_otp_submit(Page(candidate)))
        self.assertTrue(candidate.clicked)

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

    def test_marks_automatic_sms_after_qr_as_otp_flow(self) -> None:
        with TemporaryDirectory() as directory:
            manager = DouyinQRAuthManager(Path(directory))
            session = QRLoginSession(
                session_id="test-session",
                image_path=Path(directory) / "login.png",
                status="waiting_scan",
            )
            manager._session = session

            manager._mark_waiting_for_otp(session)
            snapshot = manager.snapshot("test-session")

            self.assertEqual(snapshot["status"], "waiting_otp")
            self.assertTrue(snapshot["otp_requested_after_qr"])
            self.assertTrue(snapshot["otp_required"])
            self.assertTrue(snapshot["can_submit_otp"])
            self.assertFalse(snapshot["can_resend_otp"])

    def test_queues_resend_for_qr_second_factor_without_exposing_input(self) -> None:
        with TemporaryDirectory() as directory:
            manager = DouyinQRAuthManager(Path(directory))
            session = QRLoginSession(
                session_id="test-session",
                image_path=Path(directory) / "login.png",
                status="waiting_otp",
                otp_requested_after_qr=True,
            )
            manager._session = session

            snapshot = manager.resend_otp("test-session")

            self.assertEqual(snapshot["status"], "sending_code")
            command, payload = session.commands.get_nowait()
            self.assertEqual(command, "resend_otp")
            self.assertEqual(payload, {})

    def test_clicks_second_factor_resend_control(self) -> None:
        class Target:
            clicked = False

            def is_visible(self) -> bool:
                return True

            def click(self, **_kwargs: object) -> None:
                self.clicked = True

        class Locator:
            def __init__(self, target: Target | None = None) -> None:
                self.target = target

            @property
            def last(self) -> "Locator":
                return self

            def count(self) -> int:
                return 1 if self.target is not None else 0

            def nth(self, _index: int) -> Target:
                assert self.target is not None
                return self.target

            def get_by_text(self, text: str, **_kwargs: object) -> "Locator":
                return Locator(target if text == "重新发送" else None)

        class Page:
            def locator(self, selector: str) -> Locator:
                return Locator(target) if selector == "#uc-second-verify" else Locator()

            def wait_for_timeout(self, _milliseconds: int) -> None:
                pass

        target = Target()
        with TemporaryDirectory() as directory:
            manager = DouyinQRAuthManager(Path(directory))
            session = QRLoginSession(
                session_id="test-session",
                image_path=Path(directory) / "login.png",
                status="sending_code",
                otp_requested_after_qr=True,
            )
            manager._session = session

            manager._resend_second_factor_otp(Page(), session)

            self.assertTrue(target.clicked)
            self.assertEqual(session.status, "waiting_otp")

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
            self.assertIsNone(otp_snapshot["browser_otp_input_length"])
            self.assertFalse(otp_snapshot["browser_otp_submit_clicked"])

    def test_queues_direct_login_gesture_without_exposing_input(self) -> None:
        with TemporaryDirectory() as directory:
            manager = DouyinQRAuthManager(Path(directory))
            session = QRLoginSession(
                session_id="test-session",
                image_path=Path(directory) / "login.png",
                status="waiting_otp",
            )
            manager._session = session

            snapshot = manager.interact(
                "test-session",
                action="click",
                x_ratio=0.5,
                y_ratio=0.25,
            )
            self.assertTrue(snapshot["direct_control_available"])
            command, payload = session.commands.get_nowait()
            self.assertEqual(command, "interact")
            self.assertEqual(payload["action"], "click")
            self.assertNotIn("otp", repr(payload).lower())
            payload.clear()

            manager.interact("test-session", action="key", key="5")
            command, payload = session.commands.get_nowait()
            self.assertEqual(command, "interact")
            self.assertEqual(payload["key"], "5")
            payload.clear()


if __name__ == "__main__":
    unittest.main()
