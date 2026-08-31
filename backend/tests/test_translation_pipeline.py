import unittest

from pipeline.translation import contains_han, translate_segments


class MarkerTranslator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def translate(self, text: str) -> str:
        self.calls.append(text)
        return text.replace("你好", "Xin chào").replace("世界", "thế giới")


class BrokenBatchTranslator:
    def translate(self, text: str) -> str:
        if "[[VTS:" in text:
            return "marker đã bị mất"
        return {"你好": "Xin chào", "世界": "thế giới"}[text]


class AlwaysFailTranslator:
    def translate(self, text: str) -> str:
        raise RuntimeError("rate limited")


class TranslationPipelineTests(unittest.TestCase):
    segments = [
        {"start": 0, "end": 1, "text": "你好"},
        {"start": 1, "end": 2, "text": "世界"},
    ]

    def test_batches_multiple_cues_into_one_request(self) -> None:
        translator = MarkerTranslator()

        translated = translate_segments(self.segments, translator, sleeper=lambda _: None)

        self.assertEqual([item["text"] for item in translated], ["Xin chào", "thế giới"])
        self.assertEqual(len(translator.calls), 1)
        self.assertTrue(all(item["translation_status"] == "translated" for item in translated))

    def test_falls_back_to_individual_translation_when_markers_are_changed(self) -> None:
        translated = translate_segments(
            self.segments,
            BrokenBatchTranslator(),
            retries=1,
            sleeper=lambda _: None,
        )

        self.assertEqual([item["text"] for item in translated], ["Xin chào", "thế giới"])

    def test_marks_source_fallback_for_review_instead_of_hiding_failure(self) -> None:
        translated = translate_segments(
            self.segments,
            AlwaysFailTranslator(),
            retries=1,
            sleeper=lambda _: None,
        )

        self.assertEqual(translated[0]["text"], "你好")
        self.assertEqual(translated[0]["translation_status"], "source_fallback")
        self.assertTrue(translated[0]["needs_review"])

    def test_detects_cjk_text(self) -> None:
        self.assertTrue(contains_han("Tên riêng 炭治郎"))
        self.assertFalse(contains_han("Tiếng Việt đầy đủ"))


if __name__ == "__main__":
    unittest.main()
