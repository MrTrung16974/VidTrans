import unittest
import json
import tempfile
from pathlib import Path

from pipeline.tiktok import LocalExtractiveTikTokProvider, render_tiktok_text, write_tiktok_artifacts


class TikTokSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = LocalExtractiveTikTokProvider()
        self.segments = [
            {"text": "Không ai có thể quyết định cuộc đời thay cho bạn."},
            {"text": "Điều quan trọng là hiểu mình thực sự muốn trở thành người như thế nào."},
            {"text": "Mỗi lựa chọn hôm nay sẽ tạo nên tương lai của chính bạn."},
            {"text": "Điều quan trọng là hiểu mình thực sự muốn trở thành người như thế nào."},
            {"text": "Hãy kiên trì với con đường mình đã lựa chọn."},
        ]

    def test_generates_complete_deterministic_post(self) -> None:
        first = self.provider.generate(self.segments, max_summary_chars=220, hashtag_count=6)
        second = self.provider.generate(self.segments, max_summary_chars=220, hashtag_count=6)

        self.assertEqual(first, second)
        self.assertTrue(first.title)
        self.assertTrue(first.hook)
        self.assertLessEqual(len(first.summary), 220)
        self.assertEqual(first.source_cues, 4)
        self.assertEqual(len(first.hashtags), 6)
        self.assertIn("#vietsub", first.hashtags)
        self.assertIn("#tiengtrung", first.hashtags)

    def test_low_confidence_cue_is_deprioritized_for_hook(self) -> None:
        post = self.provider.generate(
            [
                {"text": "Một câu rất nổi bật về thành công và lựa chọn.", "needs_review": True},
                {"text": "Kiên trì giúp chúng ta tiến gần hơn đến mục tiêu.", "needs_review": False},
            ],
            max_summary_chars=180,
            hashtag_count=4,
        )
        self.assertIn("Kiên trì", post.hook)

    def test_hashtags_are_ascii_and_unique(self) -> None:
        post = self.provider.generate(self.segments, max_summary_chars=180, hashtag_count=8)
        self.assertEqual(len(post.hashtags), len(set(post.hashtags)))
        for hashtag in post.hashtags:
            self.assertRegex(hashtag, r"^#[a-z0-9]+$")

    def test_rendered_text_contains_copy_ready_sections(self) -> None:
        post = self.provider.generate(self.segments, max_summary_chars=180, hashtag_count=5)
        rendered = render_tiktok_text(post)
        self.assertIn("TIÊU ĐỀ\n", rendered)
        self.assertIn("TÓM TẮT\n", rendered)
        self.assertIn("CAPTION\n", rendered)

    def test_writes_json_and_copy_ready_text_artifacts(self) -> None:
        post = self.provider.generate(self.segments, max_summary_chars=180, hashtag_count=5)
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path, text_path = write_tiktok_artifacts(post, Path(temp_dir), "job-123")
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            rendered = text_path.read_text(encoding="utf-8")

        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["generator"], "local-extractive-v1")
        self.assertEqual(payload["caption"], post.caption)
        self.assertIn(post.summary, rendered)

    def test_rejects_empty_content(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty translation"):
            self.provider.generate([], max_summary_chars=200, hashtag_count=5)

    def test_validates_limits(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_summary_chars"):
            self.provider.generate(self.segments, max_summary_chars=20, hashtag_count=5)
        with self.assertRaisesRegex(ValueError, "hashtag_count"):
            self.provider.generate(self.segments, max_summary_chars=200, hashtag_count=20)

    def test_zero_hashtags_produces_a_hashtag_free_caption(self) -> None:
        post = self.provider.generate(
            [{"text": "Một nội dung đủ rõ ràng để tạo bản tóm tắt."}],
            max_summary_chars=100,
            hashtag_count=0,
        )

        self.assertEqual(post.hashtags, [])
        self.assertNotIn("#", post.caption)


if __name__ == "__main__":
    unittest.main()
