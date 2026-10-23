import unittest
from dataclasses import replace

import main
from pipeline.tiktok import LocalExtractiveTikTokProvider


class TikTokAutoPublishDescriptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.post = LocalExtractiveTikTokProvider().generate(
            [
                {"text": "Không ai có thể quyết định cuộc đời thay cho bạn."},
                {"text": "Điều quan trọng là hiểu mình thực sự muốn trở thành người như thế nào."},
                {"text": "Mỗi lựa chọn hôm nay sẽ tạo nên tương lai của chính bạn."},
            ],
            max_summary_chars=220,
            hashtag_count=5,
        )

    def test_description_is_the_full_caption_with_hashtags(self) -> None:
        description = main.tiktok_post_description(self.post)

        self.assertEqual(description, self.post.caption.strip())
        self.assertNotEqual(description, self.post.title)
        self.assertTrue(self.post.hashtags)
        for hashtag in self.post.hashtags:
            self.assertIn(hashtag, description)

    def test_falls_back_to_title_and_handles_missing_post(self) -> None:
        self.assertEqual(main.tiktok_post_description(replace(self.post, caption="")), self.post.title)
        self.assertEqual(main.tiktok_post_description(None), "")


if __name__ == "__main__":
    unittest.main()
