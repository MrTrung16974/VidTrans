import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from domain.social_publishing import PublishRequest, PublishStatus
from infrastructure.publishers.pinterest import (
    PinterestConfigurationError,
    PinterestPublisher,
    PinterestPublisherConfig,
)


class FakePinterestAPI:
    def __init__(self):
        self.calls = []
        self.media_status = "succeeded"

    def __call__(self, method, url, headers, body):
        parsed = urllib.parse.urlsplit(url)
        self.calls.append((method, parsed.path, headers, body))
        if parsed.netloc == "uploads.pinterest.test":
            return 204, b""
        if parsed.path == "/v5/media" and method == "POST":
            return 201, json.dumps({
                "media_id": "media-1",
                "upload_url": "https://uploads.pinterest.test/video",
                "upload_parameters": {"key": "upload-key", "policy": "policy"},
            }).encode()
        if parsed.path == "/v5/media/media-1":
            return 200, json.dumps({"status": self.media_status}).encode()
        if parsed.path == "/v5/pins":
            return 201, json.dumps({"id": "pin-1"}).encode()
        return 404, json.dumps({"message": "not found"}).encode()


class PinterestPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.video = Path(self.temporary.name) / "video.mp4"
        self.video.write_bytes(b"video-data")
        self.api = FakePinterestAPI()
        self.publisher = PinterestPublisher(
            config=PinterestPublisherConfig(
                access_token="token", default_board_id="board-1",
                api_base="https://api.pinterest.test/v5",
                poll_attempts=2, poll_interval_seconds=0,
            ),
            requester=self.api,
            sleeper=lambda _seconds: None,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_publish_uploads_video_and_creates_pin(self):
        checkpoints = []
        progress = []
        result = self.publisher.publish(
            PublishRequest(
                video_path=self.video,
                caption="Mô tả Pin",
                options={
                    "title": "Tiêu đề",
                    "cover_image_url": "https://cdn.example.test/cover.jpg",
                    "link": "https://example.test/article",
                },
            ),
            on_checkpoint=lambda media, post: checkpoints.append((media, post)),
            on_progress=lambda current, total: progress.append((current, total)),
        )

        self.assertEqual(result.status, PublishStatus.PUBLISHED)
        self.assertEqual(result.remote_post_id, "pin-1")
        self.assertEqual(checkpoints, [("media-1", None), ("media-1", "pin-1")])
        self.assertEqual(progress, [(10, 10)])
        upload = next(call for call in self.api.calls if call[1] == "/video")
        self.assertIn(b'name="file"; filename="video.mp4"', upload[3])
        create = next(call for call in self.api.calls if call[1] == "/v5/pins")
        payload = json.loads(create[3].decode())
        self.assertEqual(payload["board_id"], "board-1")
        self.assertEqual(payload["media_source"]["media_id"], "media-1")

    def test_video_pin_requires_https_cover_before_api_call(self):
        with self.assertRaisesRegex(PinterestConfigurationError, "cover_image_url HTTPS"):
            self.publisher.publish(
                PublishRequest(video_path=self.video, caption="caption"),
                on_checkpoint=lambda _media, _post: None,
            )
        self.assertEqual(self.api.calls, [])

    def test_reconcile_reports_remote_processing(self):
        self.api.media_status = "registered"
        result = self.publisher.reconcile(
            remote_media_id="media-1", remote_post_id=None, account_id=None,
        )
        self.assertEqual(result.status, PublishStatus.PROCESSING_REMOTE)
        self.assertEqual(result.remote_media_id, "media-1")


if __name__ == "__main__":
    unittest.main()
