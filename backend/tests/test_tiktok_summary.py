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
        self.assertLessEqual(len(first.hashtags), 5)
        self.assertIn("#luachon", first.hashtags)
        self.assertNotIn("#tiengtrung", first.hashtags)

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
        self.assertEqual(payload["generator"], "local-extractive-v2")
        self.assertEqual(payload["caption"], post.caption)
        self.assertIn(post.summary, rendered)

    def test_caption_is_short_and_does_not_repeat_the_hook(self):
        post = self.provider.generate(self.segments, max_summary_chars=350, hashtag_count=5)
        paragraphs = post.caption.split("\n\n")
        self.assertLessEqual(len(paragraphs), 3)
        self.assertEqual(len(paragraphs), len(set(paragraphs)))
        self.assertLessEqual(len("\n\n".join(p for p in paragraphs if not p.startswith("#"))), 350)

    def test_tags_use_relevant_phrases_not_syllables_or_generic_tags(self):
        post = self.provider.generate([
            {"text": "Công thức nấu ăn này giúp cả gia đình có bữa tối ngon miệng."},
            {"text": "Cho cà chua vào nồi rồi đảo đều trong hai phút."},
        ], hashtag_count=12)
        self.assertIn("#nauan", post.hashtags)
        self.assertIn("#congthuc", post.hashtags)
        self.assertNotIn("#cho", post.hashtags)
        self.assertNotIn("#tiengtrung", post.hashtags)
        self.assertNotIn("#fyp", post.hashtags)
        self.assertLessEqual(len(post.hashtags), 5)

    def test_unknown_topic_does_not_get_unrelated_hashtags(self):
        post = self.provider.generate([{"text": "Ốc vít cần được siết lại bằng dụng cụ chuyên dụng."}])
        self.assertEqual(post.hashtags, [])

    def test_review_flagged_content_does_not_enter_caption_or_tags(self):
        post = self.provider.generate([
            {"text": "Du lịch du lịch du lịch du lịch du lịch.", "needs_review": True},
            {"text": "Công thức nấu ăn này rất dễ thực hiện."},
        ])
        self.assertNotIn("du lịch", post.caption.lower())
        self.assertNotIn("#dulich", post.hashtags)
        self.assertEqual(post.source_cues, 2)

    def test_deduplicates_punctuation_variants(self):
        post = self.provider.generate([
            {"text": "Kiên trì giúp bạn tiến gần hơn tới mục tiêu."},
            {"text": "Kiên trì giúp bạn tiến gần hơn tới mục tiêu!"},
        ])
        self.assertEqual(post.source_cues, 1)

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
