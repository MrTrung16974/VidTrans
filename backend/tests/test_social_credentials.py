import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.admin_accounts import AdminAccountStore
from infrastructure.social_credentials import SocialCredentialError, SocialCredentialStore
from infrastructure.credential_cipher import CredentialCipher
from infrastructure.publishers.encrypted_oauth_store import EncryptedOAuthAccountStore
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from application.social_connection_routes import create_social_connection_router
from infrastructure.publishers.youtube import YouTubePublisher, YouTubePublisherConfig
from infrastructure.publishers.facebook import FacebookPublisher, FacebookPublisherConfig
from domain.social_publishing import PublishRequest, PublishStatus
import json
import urllib.parse


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

    def test_encrypted_oauth_adapter_is_user_scoped_and_preserves_refresh_token(self) -> None:
        cipher = CredentialCipher(Fernet.generate_key().decode("ascii"), key_id="test-key")
        owner_store = EncryptedOAuthAccountStore(
            self.store, cipher, user_id=self.owner["user_id"], platform="YOUTUBE"
        )
        other_store = EncryptedOAuthAccountStore(
            self.store, cipher, user_id=self.other["user_id"], platform="YOUTUBE"
        )
        owner_store.save("channel-1", {
            "account_id": "channel-1", "name": "Owner channel",
            "access_token": "access-1", "refresh_token": "refresh-1",
            "expires_at": 2_000_000_000, "scope": "youtube.upload youtube.readonly",
        })
        self.assertEqual(owner_store.load("channel-1")["access_token"], "access-1")
        self.assertEqual(other_store.all(), [])

        owner_store.save("channel-1", {
            "account_id": "channel-1", "name": "Owner channel",
            "access_token": "access-2", "expires_at": 2_000_000_100,
        })
        refreshed = owner_store.load("channel-1")
        self.assertEqual(refreshed["access_token"], "access-2")
        self.assertEqual(refreshed["refresh_token"], "refresh-1")

        owner_store.delete("channel-1")
        self.assertIsNone(owner_store.load("channel-1"))

    def test_connection_api_never_returns_another_users_connection_or_tokens(self) -> None:
        saved = self.store.upsert_connection(
            user_id=self.owner["user_id"], platform="FACEBOOK", provider_account_id="page-1",
            display_name="Owner Page", access_token_ciphertext=b"ciphertext",
            encryption_key_id="primary",
        )
        app = FastAPI()
        app.include_router(create_social_connection_router(
            self.store, owner_resolver=lambda request: request.headers["x-test-user"]
        ))
        client = TestClient(app)
        owner_list = client.get(
            "/api/v1/social-connections", headers={"x-test-user": self.owner["user_id"]}
        )
        self.assertEqual(owner_list.status_code, 200)
        self.assertEqual(owner_list.json()["items"][0]["display_name"], "Owner Page")
        self.assertNotIn("access_token", str(owner_list.json()))
        self.assertEqual(
            client.get(
                f"/api/v1/social-connections/{saved['connection_id']}",
                headers={"x-test-user": self.other["user_id"]},
            ).status_code,
            404,
        )

    def test_youtube_self_service_oauth_encrypts_token_and_can_publish(self) -> None:
        cipher = CredentialCipher(Fernet.generate_key().decode("ascii"), key_id="youtube-key")
        account_store = EncryptedOAuthAccountStore(
            self.store, cipher, user_id=self.owner["user_id"], platform="YOUTUBE"
        )
        video = self.database.parent / "youtube.mp4"
        video.write_bytes(b"video")

        def requester(method, url, headers, body):
            if url == "https://oauth2.googleapis.com/token":
                return 200, json.dumps({
                    "access_token": "access-secret", "refresh_token": "refresh-secret",
                    "expires_in": 3600,
                }).encode()
            if "/channels?" in url:
                return 200, b'{"items":[{"id":"channel-1","snippet":{"title":"My Channel"}}]}'
            if "upload/youtube" in url:
                return 200, b'{"id":"video-1"}'
            if "/videos?" in url:
                return 200, b'{"items":[{"status":{"uploadStatus":"processed"},"processingDetails":{"processingStatus":"succeeded"}}]}'
            return 404, b'{}'

        publisher = YouTubePublisher(
            self.database.parent / "unused-youtube-files",
            config=YouTubePublisherConfig("client", "secret", "https://vidtrans.test/callback"),
            requester=requester,
            account_store=account_store,
        )
        authorization_url = publisher.authorization_url()
        state = urllib.parse.parse_qs(urllib.parse.urlparse(authorization_url).query)["state"][0]
        status = publisher.exchange_code("authorization-code", state)
        self.assertTrue(status["connected"])
        self.assertTrue(status["connection_id"])

        public_connection = self.store.get_for_user(
            user_id=self.owner["user_id"], connection_id=status["connection_id"]
        )
        self.assertNotIn("access_token_ciphertext", public_connection)
        encrypted = self.store.load_encrypted_tokens(
            user_id=self.owner["user_id"], connection_id=status["connection_id"]
        )
        self.assertNotIn(b"access-secret", bytes(encrypted["access_token_ciphertext"]))

        result = publisher.publish(
            PublishRequest(
                video, "Description", account_id="channel-1",
                options={"title": "Video title", "privacy": "private"},
            ),
            on_checkpoint=lambda *_args: None,
        )
        self.assertEqual(result.remote_post_id, "video-1")
        reconciled = publisher.reconcile(
            remote_media_id="video-1", remote_post_id="video-1", account_id="channel-1"
        )
        self.assertEqual(reconciled.status, PublishStatus.PUBLISHED)

    def test_facebook_self_service_oauth_encrypts_page_token_and_can_publish(self) -> None:
        cipher = CredentialCipher(Fernet.generate_key().decode("ascii"), key_id="facebook-key")
        account_store = EncryptedOAuthAccountStore(
            self.store, cipher, user_id=self.owner["user_id"], platform="FACEBOOK"
        )
        video = self.database.parent / "facebook.mp4"
        video.write_bytes(b"video")

        def requester(method, url, headers, body):
            if "/oauth/access_token" in url:
                return 200, b'{"access_token":"user-secret"}'
            if "/me/accounts" in url:
                return 200, b'{"data":[{"id":"page-1","name":"My Page","access_token":"page-secret"}]}'
            if url.endswith("/page-1/video_reels") and b"upload_phase=start" in (body or b""):
                return 200, b'{"video_id":"reel-1","upload_url":"https://upload.facebook.test/reel-1"}'
            if url == "https://upload.facebook.test/reel-1":
                return 200, b'{"success":true}'
            if url.endswith("/page-1/video_reels"):
                return 200, b'{"success":true}'
            if "/reel-1?" in url:
                return 200, b'{"status":{"video_status":"ready"}}'
            return 404, b'{}'

        publisher = FacebookPublisher(
            self.database.parent / "unused-facebook-files",
            config=FacebookPublisherConfig("client", "secret", "https://vidtrans.test/callback"),
            requester=requester,
            account_store=account_store,
        )
        authorization_url = publisher.authorization_url()
        state = urllib.parse.parse_qs(urllib.parse.urlparse(authorization_url).query)["state"][0]
        status = publisher.exchange_code("authorization-code", state)
        self.assertTrue(status["connected"])
        self.assertTrue(status["connection_id"])
        encrypted = self.store.load_encrypted_tokens(
            user_id=self.owner["user_id"], connection_id=status["connection_id"]
        )
        self.assertNotIn(b"page-secret", bytes(encrypted["access_token_ciphertext"]))

        checkpoints = []
        result = publisher.publish(
            PublishRequest(video, "Caption", account_id="page-1"),
            on_checkpoint=lambda media_id, post_id: checkpoints.append((media_id, post_id)),
        )
        self.assertEqual(result.remote_media_id, "reel-1")
        self.assertEqual(checkpoints, [("reel-1", None)])
        reconciled = publisher.reconcile(
            remote_media_id="reel-1", remote_post_id="reel-1", account_id="page-1"
        )
        self.assertEqual(reconciled.status, PublishStatus.PUBLISHED)


if __name__ == "__main__":
    unittest.main()
