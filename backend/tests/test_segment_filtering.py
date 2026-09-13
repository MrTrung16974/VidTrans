"""Tests for the meaningful segment filtering feature."""
import unittest

from pipeline.translation import (
    filter_meaningful_segments,
    is_meaningful_segment,
    translate_segments,
)


class IsMeaningfulSegmentTests(unittest.TestCase):
    """Unit tests for is_meaningful_segment()."""

    def _seg(self, text: str, **kwargs) -> dict:
        return {"start": 0.0, "end": 1.0, "text": text, **kwargs}

    # --- Should be meaningful ---

    def test_normal_chinese_sentence_is_meaningful(self) -> None:
        self.assertTrue(is_meaningful_segment(self._seg("今天天气很好")))

    def test_mixed_chinese_and_punctuation_is_meaningful(self) -> None:
        self.assertTrue(is_meaningful_segment(self._seg("你好，世界！")))

    def test_short_but_real_word_is_meaningful(self) -> None:
        self.assertTrue(is_meaningful_segment(self._seg("走")))

    # --- Should NOT be meaningful ---

    def test_empty_text_is_not_meaningful(self) -> None:
        self.assertFalse(is_meaningful_segment(self._seg("")))

    def test_whitespace_only_is_not_meaningful(self) -> None:
        self.assertFalse(is_meaningful_segment(self._seg("   ")))

    def test_pure_filler_en_is_not_meaningful(self) -> None:
        self.assertFalse(is_meaningful_segment(self._seg("嗯")))

    def test_multiple_fillers_are_not_meaningful(self) -> None:
        self.assertFalse(is_meaningful_segment(self._seg("嗯嗯啊啊")))

    def test_filler_with_punctuation_only_is_not_meaningful(self) -> None:
        self.assertFalse(is_meaningful_segment(self._seg("嗯，啊。")))

    def test_pure_number_string_is_not_meaningful(self) -> None:
        self.assertFalse(is_meaningful_segment(self._seg("123")))

    def test_low_logprob_is_filtered_out(self) -> None:
        self.assertFalse(
            is_meaningful_segment(self._seg("今天天气很好", avg_logprob=-1.5))
        )

    def test_borderline_logprob_above_threshold_is_kept(self) -> None:
        self.assertTrue(
            is_meaningful_segment(self._seg("今天天气很好", avg_logprob=-0.9))
        )

    def test_high_no_speech_prob_is_filtered_out(self) -> None:
        self.assertFalse(
            is_meaningful_segment(self._seg("嗯", no_speech_prob=0.95))
        )

    def test_realistic_content_with_high_no_speech_is_filtered(self) -> None:
        """Even real text should be filtered if Whisper says it's not speech."""
        self.assertFalse(
            is_meaningful_segment(self._seg("今天天气很好", no_speech_prob=0.85))
        )


class FilterMeaningfulSegmentsTests(unittest.TestCase):
    """Unit tests for filter_meaningful_segments()."""

    def test_meaningful_segments_pass_through_unchanged(self) -> None:
        segments = [
            {"start": 0.0, "end": 1.0, "text": "今天天气很好"},
            {"start": 1.0, "end": 2.0, "text": "明天会下雨"},
        ]
        result = filter_meaningful_segments(segments)
        self.assertEqual(len(result), 2)
        self.assertFalse(result[0]["skip_subtitle"])
        self.assertFalse(result[0]["skip_tts"])
        self.assertEqual(result[0]["text"], "今天天气很好")

    def test_filler_segments_are_flagged_not_removed(self) -> None:
        segments = [
            {"start": 0.0, "end": 0.5, "text": "嗯"},
            {"start": 0.5, "end": 1.5, "text": "今天天气很好"},
        ]
        result = filter_meaningful_segments(segments)
        self.assertEqual(len(result), 2, "list length must be preserved for timing stability")
        self.assertTrue(result[0]["skip_subtitle"])
        self.assertTrue(result[0]["skip_tts"])
        self.assertFalse(result[1]["skip_subtitle"])

    def test_hallucination_logprob_is_flagged(self) -> None:
        segments = [
            {"start": 0.0, "end": 1.0, "text": "今天", "avg_logprob": -1.8},
        ]
        result = filter_meaningful_segments(segments)
        self.assertTrue(result[0]["skip_subtitle"])

    def test_all_meaningful_returns_all_false_flags(self) -> None:
        segments = [{"start": i, "end": i + 1, "text": "你好"} for i in range(5)]
        result = filter_meaningful_segments(segments)
        self.assertTrue(all(not r["skip_subtitle"] for r in result))

    def test_all_filler_returns_all_true_flags(self) -> None:
        segments = [{"start": i, "end": i + 1, "text": "嗯啊"} for i in range(3)]
        result = filter_meaningful_segments(segments)
        self.assertTrue(all(r["skip_subtitle"] for r in result))

    def test_original_segment_fields_are_preserved(self) -> None:
        seg = {"start": 0.0, "end": 1.0, "text": "今天", "source_method": "speech", "avg_logprob": -0.3}
        result = filter_meaningful_segments([seg])
        self.assertEqual(result[0]["source_method"], "speech")
        self.assertEqual(result[0]["avg_logprob"], -0.3)


class TranslateSegmentsWithFilteredInputTests(unittest.TestCase):
    """translate_segments should skip segments marked skip_subtitle=True."""

    class EchoTranslator:
        def translate(self, text: str) -> str:
            return text.replace("你好", "Xin chào").replace("世界", "thế giới")

    def test_skipped_segments_are_not_translated(self) -> None:
        segments = [
            {"start": 0.0, "end": 0.5, "text": "嗯", "skip_subtitle": True, "skip_tts": True},
            {"start": 0.5, "end": 1.5, "text": "你好"},
        ]
        result = translate_segments(segments, self.EchoTranslator(), sleeper=lambda _: None)
        self.assertEqual(len(result), 2)
        # Filler segment: text unchanged, status = skipped
        self.assertEqual(result[0]["translation_status"], "skipped")
        self.assertEqual(result[0]["text"], "嗯")
        # Meaningful segment: translated
        self.assertEqual(result[1]["text"], "Xin chào")
        self.assertEqual(result[1]["translation_status"], "translated")

    def test_only_skipped_segments_does_not_crash(self) -> None:
        segments = [
            {"start": 0.0, "end": 1.0, "text": "嗯", "skip_subtitle": True, "skip_tts": True},
        ]
        result = translate_segments(segments, self.EchoTranslator(), sleeper=lambda _: None)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["translation_status"], "skipped")


if __name__ == "__main__":
    unittest.main()
