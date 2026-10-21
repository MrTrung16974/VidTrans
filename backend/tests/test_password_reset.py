import re
import smtplib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from infrastructure.admin_accounts import AdminAccountStore
from infrastructure.auth import AuthConfig, AuthManager, hash_password
from infrastructure.mailer import MailConfig, MailDeliveryError, SmtpMailer
from infrastructure.password_reset import (
    CODE_TTL_SECONDS,
    MAX_ATTEMPTS,
    MAX_SENDS_PER_HOUR,
    RESEND_COOLDOWN_SECONDS,
    PasswordResetStore,
)

PASSWORD = "StrongPass!123"
NEW_PASSWORD = "BrandNewPass!456"
SECRET = "test-secret-that-is-longer-than-thirty-two-bytes"


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


class PasswordResetStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.store = PasswordResetStore(Path(self.temp.name) / "db.sqlite3", SECRET, clock=self.clock)

    def test_code_is_six_digits_and_single_use(self) -> None:
        code = self.store.issue("u1")
        self.assertRegex(code, r"^\d{6}$")
        self.assertTrue(self.store.consume("u1", code))
        self.assertFalse(self.store.consume("u1", code))

    def test_code_is_stored_only_as_a_keyed_hash(self) -> None:
        code = self.store.issue("u1")
        raw = (Path(self.temp.name) / "db.sqlite3").read_bytes()
        self.assertNotIn(code.encode(), raw)

    def test_code_expires(self) -> None:
        code = self.store.issue("u1")
        self.clock.now += CODE_TTL_SECONDS + 1
        self.assertFalse(self.store.consume("u1", code))

    def test_wrong_guesses_exhaust_the_code(self) -> None:
        code = self.store.issue("u1")
        wrong = f"{(int(code) + 1) % 1_000_000:06d}"
        for _ in range(MAX_ATTEMPTS):
            self.assertFalse(self.store.consume("u1", wrong))
        self.assertFalse(self.store.consume("u1", code))

    def test_malformed_code_spends_an_attempt(self) -> None:
        code = self.store.issue("u1")
        for _ in range(MAX_ATTEMPTS):
            self.assertFalse(self.store.consume("u1", "abc"))
        self.assertFalse(self.store.consume("u1", code))

    def test_codes_are_bound_to_their_account(self) -> None:
        code = self.store.issue("u1")
        self.store.issue("u2")
        self.assertFalse(self.store.consume("u2", code))

    def test_resend_cooldown_and_hourly_cap(self) -> None:
        first = self.store.issue("u1")
        self.assertIsNone(self.store.issue("u1"))
        self.assertTrue(self.store.consume("u1", first))
        for _ in range(MAX_SENDS_PER_HOUR - 1):
            self.clock.now += RESEND_COOLDOWN_SECONDS
            self.assertIsNotNone(self.store.issue("u1"))
        self.clock.now += RESEND_COOLDOWN_SECONDS
        self.assertIsNone(self.store.issue("u1"))
        self.clock.now += 3600
        self.assertIsNotNone(self.store.issue("u1"))

    def test_new_code_replaces_the_previous_one(self) -> None:
        old = self.store.issue("u1")
        self.clock.now += RESEND_COOLDOWN_SECONDS
        new = self.store.issue("u1")
        if old != new:
            self.assertFalse(self.store.consume("u1", old))
        self.assertTrue(self.store.consume("u1", new))


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    fail = False

    def __init__(self, host, port, timeout=None, context=None) -> None:
        self.host, self.port, self.calls, self.messages = host, port, [], []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.calls.append("quit")

    def starttls(self, context=None) -> None:
        self.calls.append("starttls")

    def login(self, username, password) -> None:
        self.calls.append(("login", username))
        if FakeSMTP.fail:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, message) -> None:
        self.messages.append(message)


class MailerTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSMTP.instances, FakeSMTP.fail = [], False

    def config(self, **overrides) -> MailConfig:
        values = dict(host="smtp.test", port=587, username="bot@test.local", password="pw", sender="bot@test.local")
        values.update(overrides)
        return MailConfig(**values)

    def test_starttls_login_and_send(self) -> None:
        SmtpMailer(self.config(), smtp_factory=FakeSMTP).send(to="a@test.local", subject="S", text="Mã 123456")
        smtp = FakeSMTP.instances[0]
        self.assertEqual(smtp.calls[:2], ["starttls", ("login", "bot@test.local")])
        self.assertEqual(smtp.messages[0]["To"], "a@test.local")
        self.assertIn("123456", smtp.messages[0].get_content())

    def test_ssl_mode_uses_implicit_tls_client(self) -> None:
        SmtpMailer(self.config(security="ssl", port=465), ssl_factory=FakeSMTP).send(to="a@x.io", subject="S", text="t")
        self.assertNotIn("starttls", FakeSMTP.instances[0].calls)

    def test_delivery_error_never_contains_the_body(self) -> None:
        FakeSMTP.fail = True
        with self.assertRaises(MailDeliveryError) as raised:
            SmtpMailer(self.config(), smtp_factory=FakeSMTP).send(to="a@x.io", subject="S", text="Mã 654321")
        self.assertNotIn("654321", str(raised.exception))

    def test_env_configuration(self) -> None:
        with patch.dict("os.environ", {"VIDTRANS_SMTP_HOST": "smtp.x.io", "VIDTRANS_SMTP_USERNAME": "bot@x.io",
                                       "VIDTRANS_SMTP_SECURITY": "ssl"}, clear=False):
            config = MailConfig.from_env()
        self.assertEqual((config.port, config.sender, config.configured), (465, "bot@x.io", True))
        self.assertFalse(MailConfig().configured)
        # docker-compose passes unset values as empty strings.
        with patch.dict("os.environ", {"VIDTRANS_SMTP_PORT": "", "VIDTRANS_SMTP_SECURITY": ""}):
            self.assertEqual(MailConfig.from_env().port, 587)
        with patch.dict("os.environ", {"VIDTRANS_SMTP_SECURITY": "plain"}):
            with self.assertRaises(ValueError):
                MailConfig.from_env()


class RecordingMailer:
    configured = True

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, **message) -> None:
        self.sent.append(message)


class PasswordResetApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        password_hash = hash_password(PASSWORD, iterations=100_000)
        self.accounts = AdminAccountStore(Path(self.temp.name) / "accounts.sqlite3", "owner", password_hash)
        self.editor = self.accounts.create(
            email="editor@test.local", display_name="Editor", role="editor",
            approval_status="approved", password_hash=password_hash,
        )
        self.accounts.create(
            email="pending@test.local", display_name="Pending", role="editor",
            approval_status="pending", password_hash=password_hash,
        )
        manager = AuthManager(AuthConfig(enabled=True, username="owner", password_hash=password_hash,
                                         jwt_secret=SECRET))
        self.mailer = RecordingMailer()
        patches = [
            patch.object(main, "AUTH_MANAGER", manager),
            patch.object(main, "ADMIN_ACCOUNTS", self.accounts),
            patch.object(main, "MAILER", self.mailer),
            patch.object(main, "PASSWORD_RESETS", PasswordResetStore(Path(self.temp.name) / "resets.sqlite3", SECRET)),
            patch.object(main, "_password_reset_requests", {}),
            patch.object(main.TENANT_GOVERNANCE, "audit"),
        ]
        for active in patches:
            active.start()
            self.addCleanup(active.stop)
        self.client = TestClient(main.app)

    def request_code(self, email: str = "editor@test.local"):
        return self.client.post("/api/v1/auth/password-reset/request", data={"email": email})

    def sent_code(self) -> str:
        return re.search(r"\b(\d{6})\b", self.mailer.sent[-1]["text"]).group(1)

    def confirm(self, code: str, email: str = "editor@test.local", password: str = NEW_PASSWORD):
        return self.client.post(
            "/api/v1/auth/password-reset/confirm",
            data={"email": email, "code": code, "new_password": password},
        )

    def login(self, password: str):
        return self.client.post("/api/v1/auth/token", data={"username": "editor@test.local", "password": password})

    def test_full_reset_flow_changes_password_and_revokes_sessions(self) -> None:
        old_session = TestClient(main.app)
        # Revocation compares whole seconds, so issue the old session a little earlier.
        with patch.object(main.AUTH_MANAGER, "_clock", lambda: time.time() - 5):
            old_session.post("/api/v1/auth/token", data={"username": "editor@test.local", "password": PASSWORD})
        response = self.request_code()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.mailer.sent[0]["to"], "editor@test.local")

        confirmed = self.confirm(self.sent_code())

        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(self.login(PASSWORD).status_code, 401)
        self.assertEqual(self.login(NEW_PASSWORD).status_code, 200)
        self.assertEqual(old_session.get("/api/v1/me").status_code, 401)
        main.TENANT_GOVERNANCE.audit.assert_called_once()

    def test_response_does_not_reveal_whether_the_email_exists(self) -> None:
        known = self.request_code("editor@test.local")
        unknown = self.request_code("nobody@test.local")
        pending = self.request_code("pending@test.local")
        self.assertEqual({known.status_code, unknown.status_code, pending.status_code}, {202})
        self.assertEqual(known.json(), unknown.json())
        self.assertEqual(known.json(), pending.json())
        self.assertEqual([mail["to"] for mail in self.mailer.sent], ["editor@test.local"])

    def test_wrong_code_is_rejected_and_password_unchanged(self) -> None:
        self.request_code()
        code = self.sent_code()
        response = self.confirm(f"{(int(code) + 1) % 1_000_000:06d}")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.login(PASSWORD).status_code, 200)

    def test_code_from_one_account_cannot_reset_another(self) -> None:
        self.request_code()
        self.assertEqual(self.confirm(self.sent_code(), email="pending@test.local").status_code, 400)

    def test_short_password_is_rejected_without_spending_the_code(self) -> None:
        self.request_code()
        code = self.sent_code()
        self.assertEqual(self.confirm(code, password="short").status_code, 422)
        self.assertEqual(self.confirm(code).status_code, 200)

    def test_client_rate_limit(self) -> None:
        statuses = [self.request_code("nobody@test.local").status_code for _ in range(main.PASSWORD_RESET_IP_LIMIT + 1)]
        self.assertEqual(statuses[-1], 429)
        self.assertEqual(set(statuses[:-1]), {202})

    def test_unconfigured_mail_reports_unavailable(self) -> None:
        self.mailer.configured = False
        self.assertEqual(self.request_code().status_code, 503)

    def test_reset_endpoints_are_public(self) -> None:
        self.assertNotIn(self.request_code().status_code, {401, 403})


if __name__ == "__main__":
    unittest.main()
