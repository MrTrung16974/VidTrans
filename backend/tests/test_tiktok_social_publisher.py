import tempfile
import unittest
from pathlib import Path

from domain.social_publishing import PublishMode, PublishRequest, PublishStatus
from infrastructure.publishers.tiktok import TikTokSocialPublisher


class FakeTikTokPublisher:
    def __init__(self) -> None:
        self.calls = []
        self.status = {"status": "PROCESSING_UPLOAD"}

    def publish(self, path, caption, **options):
        self.calls.append(("direct", path, caption, options))
        options["on_initialized"]("publish-direct")
        options["on_progress"](5, 10)
        return {
            "publish_id": "publish-direct",
            "status": "PROCESSING_UPLOAD",
            "status_payload": {"status": "PROCESSING_UPLOAD", "private_secret": "omit"},
        }

    def upload_draft(self, path, **options):
        self.calls.append(("draft", path, options))
        options["on_initialized"]("publish-draft")
        return {
            "publish_id": "publish-draft",
            "status": "SEND_TO_USER_INBOX",
            "status_payload": {"status": "SEND_TO_USER_INBOX"},
        }

    def fetch_status(self, publish_id, account_id=None):
        self.calls.append(("status", publish_id, account_id))
        return self.status


class TikTokSocialPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.video = Path(self.temporary.name) / "video.mp4"
        self.video.write_bytes(b"video")
        self.legacy = FakeTikTokPublisher()
        self.adapter = TikTokSocialPublisher(self.legacy)

    def tearDown(self):
        self.temporary.cleanup()

    def test_direct_publish_preserves_options_and_checkpoints(self):
        checkpoints = []
        progress = []
        result = self.adapter.publish(
            PublishRequest(
                video_path=self.video,
                caption="Caption\n#video",
                mode=PublishMode.DIRECT_NOW,
                account_id="creator-one",
                options={
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_comment": True,
                },
            ),
            on_checkpoint=lambda media_id, post_id: checkpoints.append((media_id, post_id)),
            on_progress=lambda sent, total: progress.append((sent, total)),
        )

        self.assertEqual(result.status, PublishStatus.PROCESSING_REMOTE)
        self.assertEqual(result.remote_media_id, "publish-direct")
        self.assertEqual(checkpoints, [("publish-direct", None)])
        self.assertEqual(progress, [(5, 10)])
        options = self.legacy.calls[0][3]
        self.assertEqual(options["privacy_level"], "PUBLIC_TO_EVERYONE")
        self.assertTrue(options["disable_comment"])
        self.assertEqual(options["account_id"], "creator-one")
        self.assertNotIn("private_secret", result.status_detail)

    def test_draft_maps_to_terminal_draft_delivery(self):
        result = self.adapter.publish(
            PublishRequest(
                video_path=self.video,
                caption="",
                mode=PublishMode.DRAFT,
                account_id="creator-one",
            ),
            on_checkpoint=lambda *_args: None,
        )

        self.assertEqual(self.legacy.calls[0][0], "draft")
        self.assertEqual(result.status, PublishStatus.DRAFT_DELIVERED)
        self.assertEqual(result.remote_media_id, "publish-draft")

    def test_reconcile_maps_remote_terminal_states(self):
        self.legacy.status = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["1"]}
        published = self.adapter.reconcile(
            remote_media_id="publish-direct", remote_post_id=None, account_id="creator-one"
        )
        self.assertEqual(published.status, PublishStatus.PUBLISHED)

        self.legacy.status = {"status": "FAILED", "fail_reason": "policy"}
        failed = self.adapter.reconcile(
            remote_media_id="publish-direct", remote_post_id=None, account_id="creator-one"
        )
        self.assertEqual(failed.status, PublishStatus.FAILED)
        self.assertEqual(failed.status_detail["fail_reason"], "policy")


if __name__ == "__main__":
    unittest.main()

