import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.instagram_routes import create_instagram_router
from domain.social_publishing import PublishRequest, PublishStatus
from infrastructure.publishers.instagram import (
    InstagramAPIError,
    InstagramPublisher,
    InstagramPublisherConfig,
)
from infrastructure.signed_media import SignedMediaError, SignedMediaUrlService
from infrastructure.admin_accounts import AdminAccountStore
from infrastructure.credential_cipher import CredentialCipher
from infrastructure.publishers.encrypted_oauth_store import EncryptedOAuthAccountStore
from infrastructure.social_credentials import SocialCredentialStore
from cryptography.fernet import Fernet


class FakeInstagramAPI:
    def __init__(self) -> None:
        self.calls = []
        self.status_code = "FINISHED"

    def __call__(self, method, url, headers, body):
        parsed = urllib.parse.urlsplit(url)
        form = urllib.parse.parse_qs(body.decode("utf-8")) if body else {}
        query = urllib.parse.parse_qs(parsed.query)
        self.calls.append((method, parsed.path, query, form))
        if parsed.netloc == "api.instagram.test" and parsed.path == "/oauth/access_token":
            return 200, json.dumps({"access_token": "short-token"}).encode()
        if parsed.path == "/access_token":
            return 200, json.dumps({"access_token": "long-token", "expires_in": 3600}).encode()
        if parsed.path == "/v24.0/me":
            return 200, json.dumps({"user_id": "ig-123", "username": "creator"}).encode()
        if parsed.path == "/v24.0/ig-123/media":
            return 200, json.dumps({"id": "container-123"}).encode()
        if parsed.path == "/v24.0/container-123":
            return 200, json.dumps({
                "status_code": self.status_code,
                "status": "Finished" if self.status_code == "FINISHED" else "Processing",
            }).encode()
        if parsed.path == "/v24.0/ig-123/media_publish":
            return 200, json.dumps({"id": "post-123"}).encode()
        return 404, json.dumps({"error": {"message": "not found"}}).encode()


class InstagramPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output_dir = self.root / "outputs"
        self.output_dir.mkdir()
        self.video = self.output_dir / "video.mp4"
        self.video.write_bytes(b"video")
        self.now = 1_800_000_000.0
        self.media_urls = SignedMediaUrlService(
            self.output_dir,
            public_base_url="https://video.example.test",
            secret="x" * 32,
            clock=lambda: self.now,
        )
        self.api = FakeInstagramAPI()
        self.config = InstagramPublisherConfig(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="https://video.example.test/api/v1/instagram-auth/callback",
            api_version="v24.0",
            graph_base="https://graph.instagram.test",
            authorize_url="https://instagram.test/oauth/authorize",
            token_url="https://api.instagram.test/oauth/access_token",
            poll_attempts=2,
            poll_interval_seconds=0,
        )
        self.publisher = InstagramPublisher(
            self.root / "auth",
            self.media_urls,
            config=self.config,
            requester=self.api,
            clock=lambda: self.now,
            sleeper=lambda _seconds: None,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def connect(self):
        authorization_url = self.publisher.authorization_url()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(authorization_url).query)
        state = query["state"][0]
        return self.publisher.exchange_code("auth-code", state)

    def test_oauth_exchange_discovers_and_stores_account(self):
        status = self.connect()

        self.assertTrue(status["connected"])
        self.assertEqual(status["account_id"], "ig-123")
        self.assertEqual(status["username"], "creator")
        self.assertEqual(self.publisher.accounts()[0]["account_id"], "ig-123")

    def test_encrypted_connection_vertical_slice_connects_and_publishes(self):
        database = self.root / "connections.sqlite3"
        owner = AdminAccountStore(database, "owner").list()[0]
        credentials = SocialCredentialStore(database)
        store = EncryptedOAuthAccountStore(
            credentials,
            CredentialCipher(Fernet.generate_key().decode("ascii"), key_id="instagram-key"),
            user_id=str(owner["user_id"]),
            platform="INSTAGRAM",
            clock=lambda: self.now,
        )
        publisher = InstagramPublisher(
            self.root / "encrypted-auth",
            self.media_urls,
            config=self.config,
            requester=self.api,
            clock=lambda: self.now,
            sleeper=lambda _seconds: None,
            account_store=store,
        )
        state = urllib.parse.parse_qs(
            urllib.parse.urlsplit(publisher.authorization_url()).query
        )["state"][0]
        status = publisher.exchange_code("auth-code", state)

        self.assertTrue(status["connected"])
        self.assertTrue(status["connection_id"])
        self.assertFalse((self.root / "encrypted-auth" / "accounts" / "ig-123.json").exists())
        encrypted = credentials.load_encrypted_tokens(
            user_id=str(owner["user_id"]), connection_id=status["connection_id"]
        )
        self.assertNotIn(b"long-token", bytes(encrypted["access_token_ciphertext"]))

        result = publisher.publish(
            PublishRequest(video_path=self.video, caption="Encrypted Reel", account_id="ig-123"),
            on_checkpoint=lambda _media_id, _post_id: None,
        )
        self.assertEqual(result.status, PublishStatus.PUBLISHED)
        self.assertEqual(result.remote_post_id, "post-123")

    def test_publish_reel_checkpoints_container_and_post(self):
        self.connect()
        checkpoints = []
        progress = []

        result = self.publisher.publish(
            PublishRequest(
                video_path=self.video,
                caption="Caption\n#video",
                account_id="ig-123",
                options={"share_to_feed": True},
            ),
            on_checkpoint=lambda media_id, post_id: checkpoints.append((media_id, post_id)),
            on_progress=lambda current, total: progress.append((current, total)),
        )

        self.assertEqual(result.status, PublishStatus.PUBLISHED)
        self.assertEqual(result.remote_post_id, "post-123")
        self.assertEqual(checkpoints, [("container-123", None), ("container-123", "post-123")])
        self.assertEqual(progress, [(1, 2), (2, 2)])
        media_call = next(call for call in self.api.calls if call[1] == "/v24.0/ig-123/media")
        self.assertEqual(media_call[3]["media_type"], ["REELS"])
        self.assertEqual(media_call[3]["share_to_feed"], ["true"])
        self.assertTrue(media_call[3]["video_url"][0].startswith("https://video.example.test/"))

    def test_processing_container_reconciles_without_duplicate_publish(self):
        self.connect()
        self.api.status_code = "IN_PROGRESS"
        result = self.publisher.reconcile(
            remote_media_id="container-123",
            remote_post_id=None,
            account_id="ig-123",
        )
        self.assertEqual(result.status, PublishStatus.PROCESSING_REMOTE)
        publish_calls = [call for call in self.api.calls if call[1].endswith("/media_publish")]
        self.assertEqual(publish_calls, [])

    def test_oauth_state_is_single_use(self):
        authorization_url = self.publisher.authorization_url()
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(authorization_url).query)["state"][0]
        self.publisher.exchange_code("auth-code", state)
        with self.assertRaisesRegex(InstagramAPIError, "không hợp lệ"):
            self.publisher.exchange_code("auth-code", state)

    def test_signed_media_token_rejects_tamper_and_expiry(self):
        url = self.media_urls.create_url(self.video, ttl_seconds=60)
        token = urllib.parse.urlsplit(url).path.rsplit("/", 1)[1]
        self.assertEqual(self.media_urls.resolve(token), self.video)
        nested = self.output_dir / "users" / "user-a" / "job-a" / "video.mp4"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"tenant-video")
        nested_url = self.media_urls.create_url(nested, ttl_seconds=60)
        nested_token = urllib.parse.urlsplit(nested_url).path.rsplit("/", 1)[1]
        self.assertEqual(self.media_urls.resolve(nested_token), nested)
        with self.assertRaises(SignedMediaError):
            self.media_urls.resolve(token + "tampered")
        self.now += 61
        with self.assertRaisesRegex(SignedMediaError, "hết hạn"):
            self.media_urls.resolve(token)

    def test_auth_and_signed_media_routes(self):
        app = FastAPI()
        app.include_router(create_instagram_router(self.publisher, self.media_urls))
        client = TestClient(app)

        status = client.get("/api/v1/instagram-auth")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["configured"])

        url = self.media_urls.create_url(self.video)
        token = urllib.parse.urlsplit(url).path.rsplit("/", 1)[1]
        media = client.get(f"/api/v1/social-media/{token}")
        self.assertEqual(media.status_code, 200)
        self.assertEqual(media.content, b"video")
        self.assertEqual(media.headers["x-content-type-options"], "nosniff")


if __name__ == "__main__":
    unittest.main()

