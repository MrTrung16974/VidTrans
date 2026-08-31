import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from infrastructure.tiktok_publisher import (
    MAX_CHUNK_SIZE,
    TikTokPublisher,
    TikTokPublisherConfig,
    TikTokPublisherError,
    create_upload_plan,
)


class FakeTikTokAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, body))
        if url.endswith("/v2/oauth/token/"):
            return 200, json.dumps(
                {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 86400,
                    "refresh_expires_in": 31536000,
                    "open_id": "creator-id",
                    "scope": "user.info.basic,video.publish",
                }
            ).encode()
        if url.endswith("/creator_info/query/"):
            return 200, json.dumps(
                {
                    "data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]},
                    "error": {"code": "ok", "message": ""},
                }
            ).encode()
        if url.endswith("/video/init/"):
            return 200, json.dumps(
                {
                    "data": {"publish_id": "publish-123", "upload_url": "https://upload.test/video"},
                    "error": {"code": "ok", "message": ""},
                }
            ).encode()
        if url == "https://upload.test/video":
            return 201, b""
        if url.endswith("/status/fetch/"):
            return 200, json.dumps(
                {
                    "data": {"status": "PROCESSING_UPLOAD"},
                    "error": {"code": "ok", "message": ""},
                }
            ).encode()
        raise AssertionError(f"Unexpected request: {method} {url}")


class TikTokPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.auth_dir = Path(self.temporary.name) / "auth"
        self.api = FakeTikTokAPI()
        self.config = TikTokPublisherConfig(
            client_key="client-key",
            client_secret="client-secret",
            redirect_uri="https://creator.example/api/v1/tiktok-auth/callback",
        )
        self.publisher = TikTokPublisher(
            self.auth_dir,
            config=self.config,
            requester=self.api,
            clock=lambda: 1000.0,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def connect(self) -> None:
        url = self.publisher.authorization_url()
        state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
        self.publisher.exchange_code("oauth-code", state)

    def test_authorization_url_contains_required_publish_scope(self) -> None:
        url = self.publisher.authorization_url()
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

        self.assertEqual(query["client_key"], ["client-key"])
        self.assertIn("video.publish", query["scope"][0])
        self.assertEqual(query["redirect_uri"], [self.config.redirect_uri])
        self.assertTrue(query["state"][0])

    def test_publish_uses_generated_title_and_uploads_rendered_video(self) -> None:
        self.connect()
        video = Path(self.temporary.name) / "output.mp4"
        video.write_bytes(b"rendered-video")

        result = self.publisher.publish(
            video,
            "Tiêu đề đã gen",
            privacy_level="PUBLIC_TO_EVERYONE",
        )

        init_call = next(call for call in self.api.calls if call[1].endswith("/video/init/"))
        init_body = json.loads((init_call[3] or b"{}").decode())
        upload_call = next(call for call in self.api.calls if call[1] == "https://upload.test/video")
        self.assertEqual(init_body["post_info"]["title"], "Tiêu đề đã gen")
        self.assertEqual(upload_call[3], b"rendered-video")
        self.assertEqual(result["publish_id"], "publish-123")
        self.assertEqual(result["status"], "PROCESSING_UPLOAD")

    def test_rejects_privacy_level_not_available_to_creator(self) -> None:
        self.connect()
        video = Path(self.temporary.name) / "output.mp4"
        video.write_bytes(b"video")

        with self.assertRaisesRegex(TikTokPublisherError, "không hỗ trợ"):
            self.publisher.publish(video, "Tiêu đề", privacy_level="FOLLOWER_OF_CREATOR")

    def test_upload_plan_merges_tail_into_last_chunk(self) -> None:
        plan = create_upload_plan(MAX_CHUNK_SIZE + 123)

        self.assertEqual(plan.chunk_size, MAX_CHUNK_SIZE)
        self.assertEqual(plan.total_chunk_count, 1)

    def test_connection_status_never_exposes_tokens(self) -> None:
        self.connect()

        status = self.publisher.connection_status()

        self.assertTrue(status["connected"])
        self.assertNotIn("access_token", status)
        self.assertNotIn("refresh_token", status)


if __name__ == "__main__":
    unittest.main()
