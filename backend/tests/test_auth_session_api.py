import tempfile
from datetime import datetime, timedelta, timezone
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from infrastructure.admin_accounts import AdminAccountStore
from infrastructure.auth import AuthConfig, AuthManager, hash_password


PASSWORD = "StrongPass!123"
CSRF = {"X-VidTrans-Request": "1"}


class AuthSessionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        password_hash = hash_password(PASSWORD, iterations=100_000)
        self.accounts = AdminAccountStore(Path(self.temp.name) / "accounts.sqlite3", "owner", password_hash)
        self.editor = self.accounts.create(
            email="editor@test.local", display_name="Editor", role="editor",
            approval_status="approved", password_hash=password_hash,
        )
        self.admin = self.accounts.create(
            email="admin@test.local", display_name="Admin", role="admin",
            approval_status="approved", password_hash=password_hash,
        )
        manager = AuthManager(AuthConfig(
            enabled=True,
            username="owner",
            password_hash=password_hash,
            jwt_secret="test-secret-that-is-longer-than-thirty-two-bytes",
            token_ttl_minutes=30,
            refresh_token_ttl_days=5,
        ))
        self.patches = [
            patch.object(main, "AUTH_MANAGER", manager),
            patch.object(main, "ADMIN_ACCOUNTS", self.accounts),
            patch.object(main.TENANT_GOVERNANCE, "audit"),
        ]
        for active_patch in self.patches:
            active_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp.cleanup()

    def login(self, client: TestClient, email: str, *, remember: bool):
        return client.post(
            "/api/v1/auth/token",
            data={"username": email, "password": PASSWORD, "remember": str(remember).lower()},
        )

    def set_cookie_headers(self, response) -> list[str]:
        return response.headers.get_list("set-cookie")

    def test_remember_me_sets_persistent_cookies_with_expected_lifetimes(self) -> None:
        response = self.login(self.client, "editor@test.local", remember=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["expires_in"], 1800)
        cookies = self.set_cookie_headers(response)
        access = next(c for c in cookies if c.startswith("vidtrans_access_token="))
        refresh = next(c for c in cookies if c.startswith("vidtrans_refresh_token="))
        self.assertIn("Max-Age=1800", access)
        self.assertIn("Max-Age=432000", refresh)
        self.assertIn("Path=/api/v1/auth/", refresh)
        self.assertIn("HttpOnly", refresh)

    def test_without_remember_me_cookies_end_with_browser_session(self) -> None:
        response = self.login(self.client, "editor@test.local", remember=False)
        self.assertEqual(response.status_code, 200)
        for cookie in self.set_cookie_headers(response):
            self.assertNotIn("Max-Age", cookie)

    def test_refresh_issues_new_access_token_and_requires_csrf_header(self) -> None:
        self.login(self.client, "editor@test.local", remember=True)
        self.client.cookies.delete("vidtrans_access_token")
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/auth/refresh").status_code, 403)
        refreshed = self.client.post("/api/v1/auth/refresh", headers=CSRF)
        self.assertEqual(refreshed.status_code, 200)
        self.assertTrue(refreshed.json()["remember"])
        self.assertEqual(self.client.get("/api/v1/me").status_code, 200)

    def test_refresh_token_is_rejected_as_access_token(self) -> None:
        self.login(self.client, "editor@test.local", remember=True)
        refresh = self.client.cookies.get("vidtrans_refresh_token")
        response = self.client.get("/api/v1/me", headers={"Authorization": f"Bearer {refresh}"})
        self.assertEqual(response.status_code, 401)

    def test_logout_clears_refresh_cookie(self) -> None:
        self.login(self.client, "editor@test.local", remember=True)
        self.client.delete("/api/v1/auth/session", headers=CSRF)
        self.assertIsNone(self.client.cookies.get("vidtrans_refresh_token"))
        self.assertEqual(self.client.post("/api/v1/auth/refresh", headers=CSRF).status_code, 401)

    def test_admin_password_reset_revokes_existing_sessions(self) -> None:
        user_client = TestClient(main.app)
        self.login(user_client, "editor@test.local", remember=True)
        self.login(self.client, "admin@test.local", remember=False)
        # Tokens carry second-resolution iat; make the reset land strictly later.
        with patch("infrastructure.admin_accounts.utc_now", return_value="2999-01-01T00:00:00+00:00"):
            response = self.client.post(
                f"/api/v1/admin/accounts/{self.editor['user_id']}/password",
                data={"new_password": "BrandNewPass!456"},
                headers=CSRF,
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(user_client.get("/api/v1/me").status_code, 401)
        self.assertEqual(user_client.post("/api/v1/auth/refresh", headers=CSRF).status_code, 401)
        relogin = user_client.post(
            "/api/v1/auth/token", data={"username": "editor@test.local", "password": "BrandNewPass!456"}
        )
        self.assertEqual(relogin.status_code, 200)

    def test_admin_cannot_reset_owner_password_but_owner_can_reset_admin(self) -> None:
        self.login(self.client, "admin@test.local", remember=False)
        owner = self.accounts.get_by_email("owner@local.vidtrans")
        denied = self.client.post(
            f"/api/v1/admin/accounts/{owner['user_id']}/password",
            data={"new_password": "BrandNewPass!456"},
            headers=CSRF,
        )
        self.assertEqual(denied.status_code, 403)
        owner_client = TestClient(main.app)
        self.login(owner_client, "owner", remember=False)
        allowed = owner_client.post(
            f"/api/v1/admin/accounts/{self.admin['user_id']}/password",
            data={"new_password": "BrandNewPass!456"},
            headers=CSRF,
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_reset_rejects_short_password(self) -> None:
        self.login(self.client, "admin@test.local", remember=False)
        response = self.client.post(
            f"/api/v1/admin/accounts/{self.editor['user_id']}/password",
            data={"new_password": "short"},
            headers=CSRF,
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()


class GuestTrialApiTests(AuthSessionApiTests):
    def setUp(self) -> None:
        super().setUp()
        main._guest_requests.clear()
        # Full-strength PBKDF2 is slow; the guest password only needs to round-trip.
        fast_hash = patch.object(main, "hash_password", lambda value: hash_password(value, iterations=100_000))
        fast_hash.start()
        self.patches.append(fast_hash)

    def test_guest_gets_random_trial_account_and_session(self) -> None:
        response = self.client.post("/api/v1/auth/guest")
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["account"]["plan"], "trial")
        self.assertEqual(payload["quota_minutes"], 5)
        self.assertTrue(payload["credentials"]["email"].endswith(f"@{main.GUEST_EMAIL_DOMAIN}"))
        self.assertTrue(self.client.get("/api/v1/auth/status").json()["authenticated"])
        other = TestClient(main.app)
        login = other.post(
            "/api/v1/auth/token",
            data={"username": payload["credentials"]["email"], "password": payload["credentials"]["password"]},
        )
        self.assertEqual(login.status_code, 200, login.text)

    def test_guest_creation_is_rate_limited_per_client(self) -> None:
        emails = {self.client.post("/api/v1/auth/guest").json()["account"]["email"]
                  for _ in range(main.GUEST_ACCOUNTS_PER_IP)}
        self.assertEqual(len(emails), main.GUEST_ACCOUNTS_PER_IP)
        self.assertEqual(self.client.post("/api/v1/auth/guest").status_code, 429)

    def test_trial_account_cannot_switch_to_free(self) -> None:
        self.client.post("/api/v1/auth/guest")
        response = self.client.post("/api/v1/billing/switch-free", headers=CSRF)
        self.assertEqual(response.status_code, 403, response.text)

    def test_retention_removes_expired_guest_but_keeps_upgraded(self) -> None:
        expired = self.client.post("/api/v1/auth/guest").json()["account"]
        upgraded = self.client.post("/api/v1/auth/guest").json()["account"]
        self.accounts.update(upgraded["user_id"], plan="creator", payment_status="active")
        later = datetime.now(timezone.utc) + timedelta(days=8)
        with patch.object(main.JOB_SERVICE, "prune_terminal", return_value={"jobs": [], "batches": []}), \
                patch.object(main.JOB_SERVICE, "status_counts", return_value={}), \
                patch.object(main, "purge_account_data") as purge:
            self.assertEqual(main.remove_expired_guest_accounts(later), 1)
        purge.assert_called_once_with(expired["user_id"])
        self.assertEqual(self.accounts.get(upgraded["user_id"])["plan"], "creator")

    def test_retention_keeps_guest_with_active_job(self) -> None:
        guest = self.client.post("/api/v1/auth/guest").json()["account"]
        later = datetime.now(timezone.utc) + timedelta(days=8)
        with patch.object(main.JOB_SERVICE, "prune_terminal", return_value={"jobs": [], "batches": []}), \
                patch.object(main.JOB_SERVICE, "status_counts", return_value={"processing": 1}):
            self.assertEqual(main.remove_expired_guest_accounts(later), 0)
        self.assertEqual(self.accounts.get(guest["user_id"])["plan"], "trial")
