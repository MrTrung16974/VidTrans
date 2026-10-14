import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.admin_accounts import AdminAccountStore
from infrastructure.social_credentials import SocialCredentialError, SocialCredentialStore


class SocialCredentialStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        test_root = Path(__file__).resolve().parents[1] / "work" / "test-social-credentials"
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp_directory = TemporaryDirectory(dir=test_root)
        self.database = Path(self.temp_directory.name) / "credentials.sqlite3"
        self.accounts = AdminAccountStore(self.database, "owner")
        self.owner = self.accounts.list()[0]
        self.other = self.accounts.create(email="other@example.com", display_name="Other")
        self.store = SocialCredentialStore(self.database)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_creates_connection_session_and_audit_tables(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertTrue({
            "social_account_connections", "social_oauth_sessions",
            "social_connection_audit_events",
        }.issubset(tables))

    def test_connections_are_scoped_to_the_vidtrans_user_and_hide_tokens(self) -> None:
        saved = self.store.upsert_connection(
            user_id=self.owner["user_id"], platform="youtube", provider_account_id="channel-1",
            display_name="My channel", access_token_ciphertext=b"encrypted-access",
            refresh_token_ciphertext=b"encrypted-refresh", encryption_key_id="key-2026-01",
            scopes=["youtube.upload", "youtube.readonly"],
        )
        self.assertEqual(saved["platform"], "YOUTUBE")
        self.assertNotIn("access_token_ciphertext", saved)
        self.assertNotIn("refresh_token_ciphertext", saved)
        self.assertEqual(self.store.list_for_user(user_id=self.other["user_id"]), [])
        with self.assertRaisesRegex(SocialCredentialError, "Không tìm thấy"):
            self.store.get_for_user(user_id=self.other["user_id"], connection_id=saved["connection_id"])
        secret = self.store.load_encrypted_tokens(
            user_id=self.owner["user_id"], connection_id=saved["connection_id"]
        )
        self.assertEqual(bytes(secret["access_token_ciphertext"]), b"encrypted-access")

    def test_upsert_preserves_refresh_token_when_provider_returns_no_new_one(self) -> None:
        first = self.store.upsert_connection(
            user_id=self.owner["user_id"], platform="YOUTUBE", provider_account_id="channel-1",
            access_token_ciphertext=b"access-1", refresh_token_ciphertext=b"refresh-1",
            encryption_key_id="primary",
        )
        second = self.store.upsert_connection(
            user_id=self.owner["user_id"], platform="YOUTUBE", provider_account_id="channel-1",
            access_token_ciphertext=b"access-2", encryption_key_id="primary",
        )
        self.assertEqual(first["connection_id"], second["connection_id"])
        secret = self.store.load_encrypted_tokens(
            user_id=self.owner["user_id"], connection_id=first["connection_id"]
        )
        self.assertEqual(bytes(secret["access_token_ciphertext"]), b"access-2")
        self.assertEqual(bytes(secret["refresh_token_ciphertext"]), b"refresh-1")

    def test_oauth_state_is_hashed_single_use_and_bound_to_platform_and_user(self) -> None:
        expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        self.store.create_oauth_session(
            user_id=self.owner["user_id"], platform="TIKTOK", state="raw-secret-state",
            expires_at=expires, pkce_verifier_ciphertext=b"encrypted-pkce", encryption_key_id="primary",
        )
        with closing(sqlite3.connect(self.database)) as connection:
            stored_state = connection.execute("SELECT state_hash FROM social_oauth_sessions").fetchone()[0]
        self.assertNotEqual(stored_state, "raw-secret-state")
        self.assertIsNone(self.store.consume_oauth_session(platform="YOUTUBE", state="raw-secret-state"))
        session = self.store.consume_oauth_session(platform="TIKTOK", state="raw-secret-state")
        self.assertEqual(session["user_id"], self.owner["user_id"])
        self.assertIsNone(self.store.consume_oauth_session(platform="TIKTOK", state="raw-secret-state"))

    def test_revoke_wipes_encrypted_tokens_and_records_audit(self) -> None:
        saved = self.store.upsert_connection(
            user_id=self.owner["user_id"], platform="FACEBOOK", provider_account_id="page-1",
            access_token_ciphertext=b"access", refresh_token_ciphertext=b"refresh",
            encryption_key_id="primary",
        )
        revoked = self.store.revoke(user_id=self.owner["user_id"], connection_id=saved["connection_id"])
        self.assertEqual(revoked["status"], "revoked")
        secret = self.store.load_encrypted_tokens(
            user_id=self.owner["user_id"], connection_id=saved["connection_id"]
        )
        self.assertEqual(bytes(secret["access_token_ciphertext"]), b"")
        self.assertIsNone(secret["refresh_token_ciphertext"])
        with closing(sqlite3.connect(self.database)) as connection:
            actions = [row[0] for row in connection.execute(
                "SELECT action FROM social_connection_audit_events ORDER BY created_at"
            ).fetchall()]
        self.assertEqual(actions, ["connected", "revoked"])


if __name__ == "__main__":
    unittest.main()
