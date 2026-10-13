import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from domain.social_publishing import PublishRequest, PublishStatus
from infrastructure.publishers.linkedin import LinkedInPublisher, LinkedInPublisherConfig


class FakeLinkedInAPI:
    def __init__(self):
        self.calls = []
        self.video_status = "AVAILABLE"

    def __call__(self, method, url, headers, body):
        parsed = urllib.parse.urlsplit(url)
        self.calls.append((method, parsed.path, parsed.query, headers, body))
        if parsed.netloc == "upload.linkedin.test":
            return 201, b"", {"ETag": '"part-1"'}
        if parsed.path == "/rest/videos" and parsed.query == "action=initializeUpload":
            return 200, json.dumps({"value": {
                "video": "urn:li:video:video-1", "uploadToken": "upload-token",
                "uploadInstructions": [{
                    "uploadUrl": "https://upload.linkedin.test/part",
                    "firstByte": 0, "lastByte": 9,
                }],
            }}).encode(), {}
        if parsed.path == "/rest/videos" and parsed.query == "action=finalizeUpload":
            return 200, b"{}", {}
        if parsed.path == "/rest/videos/urn%3Ali%3Avideo%3Avideo-1":
            return 200, json.dumps({"status": self.video_status}).encode(), {}
        if parsed.path == "/rest/posts":
            return 201, b"{}", {"x-restli-id": "urn:li:share:post-1"}
        return 404, json.dumps({"message": "not found"}).encode(), {}


class LinkedInPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.video = Path(self.temporary.name) / "video.mp4"
        self.video.write_bytes(b"0123456789")
        self.api = FakeLinkedInAPI()
        self.publisher = LinkedInPublisher(
            config=LinkedInPublisherConfig(
                access_token="token", owner_urn="urn:li:person:owner-1",
                api_base="https://api.linkedin.test/rest", api_version="202609",
                poll_attempts=2, poll_interval_seconds=0,
            ),
            requester=self.api, sleeper=lambda _seconds: None,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_upload_finalize_and_create_video_post(self):
        checkpoints = []
        progress = []
        result = self.publisher.publish(
            PublishRequest(
                video_path=self.video, caption="Bài LinkedIn",
                options={"title": "Demo video"},
            ),
            on_checkpoint=lambda media, post: checkpoints.append((media, post)),
            on_progress=lambda current, total: progress.append((current, total)),
        )

        self.assertEqual(result.status, PublishStatus.PUBLISHED)
        self.assertEqual(result.remote_post_id, "urn:li:share:post-1")
        self.assertEqual(checkpoints, [
            ("urn:li:video:video-1", None),
            ("urn:li:video:video-1", "urn:li:share:post-1"),
        ])
        self.assertEqual(progress, [(10, 10)])
        finalize = next(call for call in self.api.calls if call[2] == "action=finalizeUpload")
        self.assertEqual(json.loads(finalize[4])["finalizeUploadRequest"]["uploadedPartIds"], ["part-1"])
        post = next(call for call in self.api.calls if call[1] == "/rest/posts")
        self.assertEqual(json.loads(post[4])["content"]["media"]["id"], "urn:li:video:video-1")
        self.assertEqual(post[3]["Linkedin-Version"], "202609")

    def test_reconcile_reports_processing_video(self):
        self.api.video_status = "PROCESSING"
        result = self.publisher.reconcile(
            remote_media_id="urn:li:video:video-1", remote_post_id=None, account_id=None,
        )
        self.assertEqual(result.status, PublishStatus.PROCESSING_REMOTE)


if __name__ == "__main__":
    unittest.main()
