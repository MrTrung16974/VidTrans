import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.social_account_routes import create_social_account_router
from domain.social_publishing import PublishRequest, PublishStatus
from infrastructure.publishers.facebook import FacebookAPIError, FacebookPublisher, FacebookPublisherConfig
from infrastructure.publishers.youtube import YouTubePublisher, YouTubePublisherConfig


class FacebookYouTubePublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"video")

    def tearDown(self):
        self.tmp.cleanup()

    def test_facebook_oauth_rejects_unknown_state(self):
        publisher = FacebookPublisher(self.root / "fb", config=FacebookPublisherConfig("id", "secret", "https://app/callback"), requester=lambda *args: (200, b"{}"))
        with self.assertRaises(FacebookAPIError):
            publisher.exchange_code("code", "wrong")

    def test_facebook_reel_publish_checkpoints_video(self):
        def request(method, url, headers, body):
            if url.endswith("/video_reels") and b"upload_phase=start" in (body or b""):
                return 200, json.dumps({"video_id": "v1", "upload_url": "https://upload"}).encode()
            if url == "https://upload" or url.endswith("/video_reels"):
                return 200, b'{"success":true}'
            return 200, b'{"status":{"video_status":"ready"}}'
        publisher = FacebookPublisher(self.root / "fb", config=FacebookPublisherConfig("id", "secret", "https://app/callback"), requester=request)
        publisher.store.save("p1", {"account_id": "p1", "access_token": "token", "name": "Page"})
        checkpoints = []
        result = publisher.publish(PublishRequest(self.video, "caption", account_id="p1"), on_checkpoint=lambda a, b: checkpoints.append((a, b)))
        self.assertEqual(result.status, PublishStatus.PROCESSING_REMOTE)
        self.assertEqual(checkpoints, [("v1", None)])

    def test_facebook_oauth_discovers_pages_without_exposing_tokens(self):
        def request(method, url, headers, body):
            if "/oauth/access_token" in url:
                return 200, b'{"access_token":"user-secret"}'
            return 200, b'{"data":[{"id":"p1","name":"Demo Page","access_token":"page-secret"}]}'
        publisher = FacebookPublisher(self.root / "fb", config=FacebookPublisherConfig("id", "secret", "https://app/callback"), requester=request)
        state = urllib.parse.parse_qs(urllib.parse.urlparse(publisher.authorization_url()).query)["state"][0]
        status = publisher.exchange_code("code", state)
        self.assertEqual(status["account_id"], "p1")
        self.assertNotIn("access_token", status)
        self.assertNotIn("access_token", publisher.accounts()[0])

    def test_youtube_upload_and_reconcile(self):
        def request(method, url, headers, body):
            if "upload/youtube" in url:
                return 200, b'{"id":"yt1"}'
            return 200, b'{"items":[{"status":{"uploadStatus":"processed"},"processingDetails":{"processingStatus":"succeeded"}}]}'
        publisher = YouTubePublisher(self.root / "yt", config=YouTubePublisherConfig("id", "secret", "https://app/callback"), requester=request)
        publisher.store.save("c1", {"account_id": "c1", "name": "Channel", "access_token": "token", "refresh_token": "refresh", "expires_at": 99999999999})
        checkpoints = []
        result = publisher.publish(PublishRequest(self.video, "description", account_id="c1", options={"title": "Title", "privacy": "private"}), on_checkpoint=lambda a, b: checkpoints.append((a, b)))
        self.assertEqual(result.remote_post_id, "yt1")
        self.assertEqual(checkpoints, [("yt1", "yt1")])
        reconciled = publisher.reconcile(remote_media_id="yt1", remote_post_id="yt1", account_id="c1")
        self.assertEqual(reconciled.status, PublishStatus.PUBLISHED)

    def test_auth_routes_return_public_account_data(self):
        class FakeError(RuntimeError):
            pass
        class FakePublisher:
            def connection_status(self, account_id=None):
                return {"configured": True, "connected": True, "account_id": account_id or "p1", "name": "Page"}
            def accounts(self):
                return [{"account_id": "p1", "name": "Page", "connected": True}]
            def authorization_url(self):
                return "https://provider.example/oauth"
            def exchange_code(self, code, state):
                return {}
            def disconnect(self, account_id):
                self.disconnected = account_id
        app = FastAPI()
        app.include_router(create_social_account_router("facebook", FakePublisher(), FakeError))
        client = TestClient(app)
        payload = client.get("/api/v1/facebook-auth").json()
        self.assertEqual(payload["accounts"][0]["account_id"], "p1")
        self.assertNotIn("access_token", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
