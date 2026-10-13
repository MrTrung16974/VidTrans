import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from domain.social_publishing import PublishRequest, PublishStatus
from infrastructure.publishers.threads import ThreadsPublisher, ThreadsPublisherConfig
from infrastructure.signed_media import SignedMediaUrlService


class FakeThreadsAPI:
    def __init__(self):
        self.calls = []
        self.container_status = "FINISHED"

    def __call__(self, method, url, headers, body):
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        form = urllib.parse.parse_qs(body.decode()) if body else {}
        self.calls.append((method, parsed.path, query, form))
        if parsed.path == "/oauth/access_token" and method == "POST":
            return 200, json.dumps({"access_token": "short"}).encode()
        if parsed.path == "/access_token":
            return 200, json.dumps({"access_token": "long", "expires_in": 3600}).encode()
        if parsed.path == "/me":
            return 200, json.dumps({"id": "threads-1", "username": "creator"}).encode()
        if parsed.path == "/threads-1/threads":
            return 200, json.dumps({"id": "container-1"}).encode()
        if parsed.path == "/container-1":
            return 200, json.dumps({"status": self.container_status}).encode()
        if parsed.path == "/threads-1/threads_publish":
            return 200, json.dumps({"id": "post-1"}).encode()
        return 404, json.dumps({"error": {"message": "not found"}}).encode()


class ThreadsPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        output = root / "outputs"
        output.mkdir()
        self.video = output / "video.mp4"
        self.video.write_bytes(b"video")
        self.now = 1_800_000_000.0
        media = SignedMediaUrlService(
            output, public_base_url="https://video.example.test", secret="s" * 32,
            clock=lambda: self.now,
        )
        self.api = FakeThreadsAPI()
        self.publisher = ThreadsPublisher(
            root / "auth", media,
            config=ThreadsPublisherConfig(
                client_id="id", client_secret="secret",
                redirect_uri="https://video.example.test/api/v1/threads-auth/callback",
                api_base="https://graph.threads.test",
                authorize_url="https://threads.test/oauth/authorize",
                token_url="https://graph.threads.test/oauth/access_token",
                poll_attempts=2, poll_interval_seconds=0,
            ),
            requester=self.api, clock=lambda: self.now, sleeper=lambda _seconds: None,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def connect(self):
        url = self.publisher.authorization_url()
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]
        return self.publisher.exchange_code("code", state)

    def test_oauth_and_account_discovery(self):
        status = self.connect()
        self.assertTrue(status["connected"])
        self.assertEqual(status["account_id"], "threads-1")
        self.assertEqual(self.publisher.accounts()[0]["username"], "creator")

    def test_video_publish_and_checkpoint(self):
        self.connect()
        checkpoints = []
        result = self.publisher.publish(
            PublishRequest(video_path=self.video, caption="Một thread mới", account_id="threads-1"),
            on_checkpoint=lambda media, post: checkpoints.append((media, post)),
        )
        self.assertEqual(result.status, PublishStatus.PUBLISHED)
        self.assertEqual(result.remote_post_id, "post-1")
        self.assertEqual(checkpoints, [("container-1", None), ("container-1", "post-1")])
        create_call = next(call for call in self.api.calls if call[1] == "/threads-1/threads")
        self.assertEqual(create_call[3]["media_type"], ["VIDEO"])
        self.assertTrue(create_call[3]["video_url"][0].startswith("https://video.example.test/"))

    def test_reconcile_waits_for_finished_container(self):
        self.connect()
        self.api.container_status = "IN_PROGRESS"
        result = self.publisher.reconcile(
            remote_media_id="container-1", remote_post_id=None, account_id="threads-1"
        )
        self.assertEqual(result.status, PublishStatus.PROCESSING_REMOTE)
        self.assertFalse(any(call[1].endswith("threads_publish") for call in self.api.calls))


if __name__ == "__main__":
    unittest.main()

