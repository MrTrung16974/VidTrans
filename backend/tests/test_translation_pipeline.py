import unittest

from pipeline.translation import (
    contains_han,
    segment_filter_signature,
    translate_segments,
    translate_segments_by_source_language,
)


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


class TimedLinesTranslator:
    supports_markers = False
    accepts_durations = True

    def __init__(self) -> None:
        self.durations: list | None = None

    def translate_lines(self, lines: list[str], durations=None) -> list[str]:
        self.durations = durations
        return [{"你好": "Xin chào", "世界": "thế giới"}[line] for line in lines]

    def translate(self, text: str) -> str:
        return self.translate_lines([text])[0]


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

    def test_llm_translator_receives_each_cue_duration(self) -> None:
        translator = TimedLinesTranslator()
        segments = [{"start": 0, "end": 1.5, "text": "你好"}, {"start": 1.5, "end": 4.0, "text": "世界"}]

        translated = translate_segments(segments, translator, sleeper=lambda _: None)

        self.assertEqual([item["text"] for item in translated], ["Xin chào", "thế giới"])
        self.assertEqual(translator.durations, [1.5, 2.5])

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

    def test_retries_cue_when_marked_batch_leaves_chinese_unchanged(self) -> None:
        class BatchThenCueTranslator:
            def translate(self, text: str) -> str:
                if "[[VTS:" in text:
                    return "[[VTS:000000]]\n\u8fd9\u5929\u4e24\u4eba\u539f\u672c\u8ba1\u5212\u4e00\u540c\u524d\u5f80\u90ca\u5916\u6e38\u73a9"
                return "Hôm đó, cả hai vốn định cùng nhau đi chơi ở ngoại ô"

        translated = translate_segments(
            [{"start": 0, "end": 1, "text": "\u8fd9\u5929\u4e24\u4eba\u539f\u672c\u8ba1\u5212\u4e00\u540c\u524d\u5f80\u90ca\u5916\u6e38\u73a9"}],
            BatchThenCueTranslator(),
            sleeper=lambda _: None,
        )

        self.assertEqual(
            translated[0]["text"],
            "Hôm đó, cả hai vốn định cùng nhau đi chơi ở ngoại ô",
        )
        self.assertEqual(translated[0]["translation_status"], "translated")

    def test_detects_cjk_text(self) -> None:
        self.assertTrue(contains_han("Tên riêng 炭治郎"))
        self.assertFalse(contains_han("Tiếng Việt đầy đủ"))

    def test_filter_signature_contains_checkpoint_inputs(self) -> None:
        signature = segment_filter_signature()
        self.assertEqual(
            set(signature),
            {"policy_version", "min_logprob", "max_no_speech_prob", "min_han_chars"},
        )
        self.assertEqual(signature["policy_version"], 2)

    def test_accepts_han_output_for_chinese_target(self) -> None:
        class EnglishToChinese:
            supports_markers = False
            def translate(self, text: str) -> str:
                return "你好世界"

        translated = translate_segments(
            [{"start": 0, "end": 1, "text": "Hello world"}],
            EnglishToChinese(),
            source_language="en",
            target_language="zh-CN",
            sleeper=lambda _: None,
        )
        self.assertEqual(translated[0]["text"], "你好世界")
        self.assertEqual(translated[0]["target_language"], "zh-CN")

    def test_translates_each_source_language_and_restores_timeline_order(self) -> None:
        class CueTranslator:
            supports_markers = False

            def __init__(self, source: str) -> None:
                self.source = source

            def translate(self, text: str) -> str:
                return "Bản dịch tiếng Trung" if self.source == "zh-CN" else "Bản dịch tiếng Hàn"

        segments = [
            {"start": 0, "end": 1, "text": "中文字幕", "source_language": "zh-CN"},
            {"start": 1, "end": 2, "text": "한국어 음성", "source_language": "ko"},
            {"start": 2, "end": 3, "text": "下一句", "source_language": "zh-CN"},
        ]
        translated = translate_segments_by_source_language(
            segments,
            target_language="vi",
            default_source_language="zh-CN",
            translator_factory=lambda source, _target: CueTranslator(source),
            sleeper=lambda _: None,
        )
        self.assertEqual(
            [item["text"] for item in translated],
            ["Bản dịch tiếng Trung", "Bản dịch tiếng Hàn", "Bản dịch tiếng Trung"],
        )
        self.assertEqual([item["start"] for item in translated], [0.0, 1.0, 2.0])


if __name__ == "__main__":
    unittest.main()


class EchoLinesTranslator:
    """LLM-style translator that, correctly, returns Latin names and numbers unchanged."""

    supports_markers = False

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.calls: list[str] = []

    def translate_lines(self, lines: list[str]) -> list[str]:
        self.lines.extend(lines)
        return [{"你好": "Xin chào"}.get(line, line) for line in lines]

    def translate(self, text: str) -> str:
        self.calls.append(text)
        return {"你好": "Xin chào"}.get(text, text)


class PreservedCueTests(unittest.TestCase):
    def cues(self, *texts: str) -> list[dict]:
        return [{"start": float(i), "end": i + 1.0, "text": text} for i, text in enumerate(texts)]

    def test_cue_without_source_script_is_kept_and_never_sent(self) -> None:
        from pipeline.translation import ensure_translation_complete

        translator = EchoLinesTranslator()
        translated = translate_segments(
            self.cues("你好", "iPhone 15 Pro", "100%", "你好"), translator,
            retries=1, sleeper=lambda _: None, source_language="zh-CN",
        )

        self.assertEqual([item["text"] for item in translated], ["Xin chào", "iPhone 15 Pro", "100%", "Xin chào"])
        self.assertEqual(translated[1]["translation_status"], "preserved")
        self.assertFalse(translated[1]["needs_review"])
        self.assertEqual(translator.lines + translator.calls, ["你好"])
        ensure_translation_complete(translated)

    def test_untranslated_source_script_still_fails_with_reason(self) -> None:
        from pipeline.translation import TranslationIncompleteError, ensure_translation_complete

        translated = translate_segments(
            self.cues("你好", "谢谢"), EchoLinesTranslator(),
            retries=1, sleeper=lambda _: None, source_language="zh-CN",
        )

        self.assertEqual(translated[1]["translation_status"], "source_fallback")
        with self.assertRaises(TranslationIncompleteError) as raised:
            ensure_translation_complete(translated)
        self.assertIn("Lỗi: unchanged_translation", str(raised.exception))
        self.assertIn("trả về nguyên văn", str(raised.exception))

    def test_latin_sources_are_still_translated(self) -> None:
        from pipeline.translation import needs_translation

        self.assertTrue(needs_translation("Hello world", "en"))
        self.assertTrue(needs_translation("こんにちは", "ja"))
        self.assertFalse(needs_translation("OK", "ja"))
        self.assertFalse(needs_translation("...!!", "en"))
        self.assertFalse(needs_translation("2024", "ko"))
