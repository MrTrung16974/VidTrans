"""Consent to /terms + /privacy: stored on sign-up, re-asked when TERMS_VERSION changes."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from domain.legal import TERMS_VERSION
from infrastructure.admin_accounts import AdminAccountStore
from infrastructure.auth import AuthConfig, AuthManager, hash_password

PASSWORD = "StrongPass!123"
CSRF = {"X-VidTrans-Request": "1"}


class TermsConsentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        password_hash = hash_password(PASSWORD, iterations=100_000)
        self.accounts = AdminAccountStore(Path(self.temp.name) / "accounts.sqlite3", "owner", password_hash)
        self.viewer = self.accounts.create(
            email="viewer@test.local", display_name="Viewer", role="viewer",
            approval_status="approved", password_hash=password_hash,
        )
        manager = AuthManager(AuthConfig(
            enabled=True, username="owner", password_hash=password_hash,
            jwt_secret="test-secret-that-is-longer-than-thirty-two-bytes",
            token_ttl_minutes=30, refresh_token_ttl_days=5,
        ))
        self.audit = patch.object(main.TENANT_GOVERNANCE, "audit")
        self.patches = [
            patch.object(main, "AUTH_MANAGER", manager),
            patch.object(main, "ADMIN_ACCOUNTS", self.accounts),
            self.audit,
        ]
        for active_patch in self.patches:
            active_patch.start()
        main._guest_requests.clear()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        main._guest_requests.clear()
        self.temp.cleanup()

    def register(self, **extra):
        data = {"display_name": "New", "email": "new@test.local", "password": PASSWORD, **extra}
        return self.client.post("/api/v1/auth/register", data=data)

    def test_register_with_consent_stores_current_version(self) -> None:
        response = self.register(accept_terms="true")
        self.assertEqual(response.status_code, 201, response.text)
        account = response.json()["account"]
        self.assertEqual(account["terms_version"], TERMS_VERSION)
        self.assertTrue(account["terms_accepted_at"])
        self.assertFalse(account["terms_outdated"])
        self.assertFalse(self.client.get("/api/v1/me").json()["terms_outdated"])

    def test_register_refusing_terms_is_rejected(self) -> None:
        response = self.register(accept_terms="false")
        self.assertEqual(response.status_code, 422)
        self.assertIsNone(self.accounts.get_by_email("new@test.local"))

    def test_older_client_without_field_still_registers_but_must_accept_later(self) -> None:
        response = self.register()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["account"]["terms_outdated"])

    def test_guest_consent(self) -> None:
        accepted = self.client.post("/api/v1/auth/guest", data={"accept_terms": "true"})
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertEqual(accepted.json()["account"]["terms_version"], TERMS_VERSION)
        refused = TestClient(main.app).post("/api/v1/auth/guest", data={"accept_terms": "false"})
        self.assertEqual(refused.status_code, 422)

    def test_accept_endpoint_records_new_version_even_for_viewer(self) -> None:
        login = self.client.post("/api/v1/auth/token", data={"username": "viewer@test.local", "password": PASSWORD})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertTrue(self.client.get("/api/v1/me").json()["terms_outdated"])
        response = self.client.post("/api/v1/me/terms/accept", headers=CSRF)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["terms_outdated"])
        self.assertEqual(self.accounts.get(self.viewer["user_id"])["terms_version"], TERMS_VERSION)
        self.assertEqual(main.TENANT_GOVERNANCE.audit.call_args.kwargs["action"],
                         "account.terms_accept")

    def test_bumping_the_version_asks_again(self) -> None:
        self.accounts.accept_terms(self.viewer["user_id"], "2000-01-01")
        self.assertTrue(main.public_admin_account(self.accounts.get(self.viewer["user_id"]))["terms_outdated"])

    def test_accept_requires_sign_in(self) -> None:
        self.assertEqual(self.client.post("/api/v1/me/terms/accept", headers=CSRF).status_code, 401)


class TermsColumnMigrationTests(unittest.TestCase):
    def test_existing_table_gains_terms_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "old.sqlite3"
            db = sqlite3.connect(path)
            db.execute("""CREATE TABLE admin_accounts (user_id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL, role TEXT NOT NULL, approval_status TEXT NOT NULL, plan TEXT NOT NULL,
                payment_status TEXT NOT NULL, provider_customer_ref TEXT, approved_at TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("INSERT INTO admin_accounts VALUES('u1','a@b.c','A','editor','approved','free','exempt',NULL,NULL,'t','t')")
            db.commit()
            db.close()
            store = AdminAccountStore(path)
            account = store.get("u1")
            self.assertIsNone(account["terms_version"])
            self.assertEqual(store.accept_terms("u1", TERMS_VERSION)["terms_version"], TERMS_VERSION)


if __name__ == "__main__":
    unittest.main()
