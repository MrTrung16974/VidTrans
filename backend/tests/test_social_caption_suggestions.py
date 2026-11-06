import json
import unittest

from infrastructure.llm_providers import LLMUnavailableError
from application.social_caption_suggestions import suggest_social_captions


SEGMENTS = [
    {"text": "Mỗi lựa chọn hôm nay sẽ tạo nên tương lai của chính bạn."},
    {"text": "Hãy kiên trì với con đường mình đã lựa chọn."},
]


class FakeLLM:
    def __init__(self, reply, name="fake-gemini"):
        self.reply = reply
        self.name = name

    def generate_json(self, system, user, schema):
        if isinstance(self.reply, Exception):
            raise self.reply
        return json.dumps(self.reply, ensure_ascii=False)


def variant(hook, tone, cues=None, hashtags=None):
    return {
        "hook": hook,
        "body": "Kiên trì với lựa chọn của mình để tiến gần hơn đến tương lai mong muốn.",
        "cta": "Bạn đang kiên trì với điều gì?",
        "hashtags": hashtags or ["#luachon", "#xuhuong"],
        "tone": tone,
        "reason": "Bám vào thông điệp lựa chọn và kiên trì.",
        "source_cues": [0, 1] if cues is None else cues,
    }


class SocialCaptionSuggestionTests(unittest.TestCase):
    def test_returns_three_grounded_ai_variants(self):
        reply = {"variants": [
            variant("Một lựa chọn nhỏ có thể đổi cả tương lai.", "curious"),
            variant("Tương lai bắt đầu từ lựa chọn hôm nay.", "emotional"),
            variant("Kiên trì là cách biến lựa chọn thành kết quả.", "expert"),
        ]}
        result = suggest_social_captions(SEGMENTS, llms=[FakeLLM(reply)])

        self.assertFalse(result.fallback)
        self.assertEqual(result.generator, "fake-gemini")
        self.assertEqual(len(result.variants), 3)
        self.assertTrue(all(len(item.caption) <= 350 for item in result.variants))

    def test_filters_hashtags_not_in_grounded_candidates(self):
        reply = {"variants": [
            variant("Lựa chọn hôm nay tạo nên ngày mai.", "natural", hashtags=["#luachon", "#viralrac"]),
            variant("Tương lai bắt đầu từ lựa chọn hôm nay.", "curious"),
            variant("Kiên trì biến lựa chọn thành kết quả.", "expert"),
        ]}
        result = suggest_social_captions(SEGMENTS, llms=[FakeLLM(reply)])

        self.assertEqual(result.variants[0].hashtags, ["#luachon"])

    def test_rejects_unrelated_number_and_falls_back(self):
        reply = {"variants": [variant("7 ngày sẽ thay đổi tương lai.", "curious")]}
        result = suggest_social_captions(SEGMENTS, llms=[FakeLLM(reply)])

        self.assertTrue(result.fallback)
        self.assertEqual(result.generator, "local-extractive-v4")

    def test_invalid_cue_indexes_fall_through_to_next_provider(self):
        bad = {"variants": [variant("Lựa chọn hôm nay tạo nên ngày mai.", "natural", cues=[99])]}
        good = {"variants": [
            variant("Kiên trì với điều bạn đã chọn.", "natural"),
            variant("Tương lai bắt đầu từ lựa chọn hôm nay.", "curious"),
            variant("Lựa chọn đúng cần được theo đuổi đến cùng.", "expert"),
        ]}
        result = suggest_social_captions(SEGMENTS, llms=[FakeLLM(bad), FakeLLM(good, "fake-qwen")])

        self.assertEqual(result.generator, "fake-qwen")
        self.assertFalse(result.fallback)

    def test_provider_failure_uses_local_fallback(self):
        result = suggest_social_captions(
            SEGMENTS, llms=[FakeLLM(LLMUnavailableError("timeout"))]
        )

        self.assertTrue(result.fallback)
        self.assertTrue(result.caption)

    def test_rejects_unknown_tone(self):
        with self.assertRaisesRegex(ValueError, "tone"):
            suggest_social_captions(SEGMENTS, tone="salesy")


if __name__ == "__main__":
    unittest.main()
