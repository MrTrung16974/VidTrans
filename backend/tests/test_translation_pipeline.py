import unittest

from pipeline.translation import (
    contains_han,
    fit_translations_to_voice,
    repair_leaked_translations,
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


class LeakRepairTests(unittest.TestCase):
    def test_only_leaked_cues_are_retranslated(self) -> None:
        segments = [
            {"source_text": "雅荣也说", "text": "雅荣 cũng nói"},
            {"source_text": "你好", "text": "Xin chào"},
            {"source_text": "啊", "text": "啊"},
            {"source_text": "世界", "text": "世 giới"},
        ]
        translator = MarkerTranslator()
        translator.translate = lambda text: {"雅荣也说": "Nhã Vinh cũng nói", "世界": "世界"}[text]

        self.assertEqual(repair_leaked_translations(segments, translator, source_language="zh-CN"), 1)
        self.assertEqual(segments[0]["text"], "Nhã Vinh cũng nói")
        self.assertEqual(segments[2]["text"], "啊")  # Filler stays; it is not dubbed.
        self.assertEqual(segments[3]["text"], "世 giới")  # Still leaking: keep the original.


if __name__ == "__main__":
    unittest.main()


class RecordingCondenser:
    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies
        self.calls: list[tuple[list[str], list[str], list[int]]] = []

    def condense_lines(self, sources, translations, max_syllables):
        self.calls.append((sources, translations, max_syllables))
        return [self.replies.get(text, text) for text in translations]


def _cue(start: float, end: float, source: str, text: str, **extra) -> dict:
    return {"start": start, "end": end, "source_text": source, "text": text, "source_language": "zh-CN", **extra}


class DubbingFitTests(unittest.TestCase):
    # Job 329da5e0: an 11-syllable line in a 1.1 s window overflowed by 0.9 s.
    LONG = "Tôi muốn biết giá một đại trắng vào ngày mai là bao nhiêu"

    def test_overlong_line_is_shortened_within_its_window(self) -> None:
        segments = [_cue(10.0, 11.12, "算个明天大白多钱已经", self.LONG), _cue(11.2, 12.5, "好", "Được")]
        condenser = RecordingCondenser({self.LONG: "Mai đại trắng giá bao nhiêu"})

        self.assertEqual(fit_translations_to_voice(segments, condenser), 1)

        self.assertEqual(condenser.calls, [(["算个明天大白多钱已经"], [self.LONG], [6])])
        self.assertEqual(segments[0]["text"], "Mai đại trắng giá bao nhiêu")
        self.assertEqual(segments[0]["text_before_fit"], self.LONG)
        self.assertEqual(segments[1]["text"], "Được")

    def test_line_that_fits_or_is_skipped_is_not_sent(self) -> None:
        segments = [
            _cue(0.0, 3.0, "我们走吧", "Chúng ta đi thôi"),
            _cue(3.0, 3.5, "嗯嗯嗯嗯", self.LONG, skip_tts=True),
        ]
        condenser = RecordingCondenser({})

        self.assertEqual(fit_translations_to_voice(segments, condenser), 0)
        self.assertEqual(condenser.calls, [])

    def test_name_left_in_chinese_is_translated_even_when_short(self) -> None:
        # Job e21f157d: TranslateGemma returned 昭丁 for 昭蒂 and the cue was never voiced.
        segments = [_cue(171.97, 172.43, "昭蒂", "昭丁")]
        condenser = RecordingCondenser({"昭丁": "Chiêu Đế"})

        self.assertEqual(fit_translations_to_voice(segments, condenser), 1)
        self.assertEqual(segments[0]["text"], "Chiêu Đế")

    def test_rewrite_that_is_longer_or_still_chinese_is_rejected(self) -> None:
        segments = [_cue(10.0, 11.12, "算个明天大白多钱已经", self.LONG), _cue(20.0, 20.4, "昭蒂", "昭丁")]
        condenser = RecordingCondenser({self.LONG: self.LONG + " vậy hả anh", "昭丁": "Chiêu 蒂"})

        self.assertEqual(fit_translations_to_voice(segments, condenser), 0)
        self.assertEqual(segments[0]["text"], self.LONG)
        self.assertEqual(segments[1]["text"], "昭丁")

    def test_condenser_failure_keeps_translations(self) -> None:
        class Broken:
            def condense_lines(self, *_args):
                raise RuntimeError("quota")

        segments = [_cue(10.0, 11.12, "算个明天大白多钱已经", self.LONG)]
        self.assertEqual(fit_translations_to_voice(segments, Broken()), 0)
        self.assertEqual(segments[0]["text"], self.LONG)


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

    def test_echoed_fragment_is_hidden_for_review_not_fatal(self) -> None:
        from pipeline.translation import ensure_translation_complete

        translated = translate_segments(
            self.cues("你好", "森"), EchoLinesTranslator(),
            retries=1, sleeper=lambda _: None, source_language="zh-CN",
        )

        fragment = translated[1]
        self.assertEqual(fragment["translation_status"], "dropped_fragment")
        self.assertTrue(fragment["skip_subtitle"] and fragment["skip_tts"] and fragment["needs_review"])
        self.assertEqual(fragment["translation_error"], "unchanged_translation")
        ensure_translation_complete(translated)

    def test_short_latin_cue_echoed_back_is_preserved(self) -> None:
        from pipeline.translation import ensure_translation_complete

        translated = translate_segments(
            self.cues("你好", "OK", "Tesla Model 3"), EchoLinesTranslator(),
            retries=1, sleeper=lambda _: None, source_language="en",
        )

        self.assertEqual([s["translation_status"] for s in translated[1:]], ["preserved", "preserved"])
        ensure_translation_complete(translated)

    def test_long_latin_cue_echoed_back_still_fails(self) -> None:
        from pipeline.translation import TranslationIncompleteError, ensure_translation_complete

        translated = translate_segments(
            self.cues("你好", "Where are you going tonight"), EchoLinesTranslator(),
            retries=1, sleeper=lambda _: None, source_language="en",
        )

        self.assertEqual(translated[1]["translation_status"], "source_fallback")
        with self.assertRaises(TranslationIncompleteError):
            ensure_translation_complete(translated)

    def test_echoed_fragment_with_stray_latin_letter_is_not_fatal(self) -> None:
        from pipeline.translation import ensure_translation_complete

        translated = translate_segments(
            self.cues("你好", "A 纪"), EchoLinesTranslator(),
            retries=1, sleeper=lambda _: None, source_language="zh-CN",
        )

        self.assertEqual(translated[1]["translation_status"], "dropped_fragment")
        ensure_translation_complete(translated)

    def test_latin_sources_are_still_translated(self) -> None:
        from pipeline.translation import needs_translation

        self.assertTrue(needs_translation("Hello world", "en"))
        self.assertTrue(needs_translation("こんにちは", "ja"))
        self.assertFalse(needs_translation("OK", "ja"))
        self.assertFalse(needs_translation("...!!", "en"))
        self.assertFalse(needs_translation("2024", "ko"))


class UnavailableLinesTranslator:
    supports_markers = False

    def __init__(self) -> None:
        self.calls = 0

    def translate_lines(self, lines):
        self.calls += 1
        from infrastructure.vietnamese_translator import TranslationUnavailableError
        raise TranslationUnavailableError("Gemini API không phản hồi sau 60 giây")

    def translate(self, text):
        return self.translate_lines([text])[0]


class UnavailableProviderTests(unittest.TestCase):
    def test_unreachable_provider_stops_instead_of_retrying_every_cue(self) -> None:
        from pipeline.translation import TranslationIncompleteError, ensure_translation_complete

        cues = [{"start": float(i), "end": i + 1.0, "text": f"第{i}句话"} for i in range(112)]
        translator = UnavailableLinesTranslator()
        translated = translate_segments(
            cues, translator, max_batch_chars=200, retries=3, sleeper=lambda _: None, source_language="zh-CN",
        )

        # One batch attempt plus one cue's retries, not 112 cues x 3 retries.
        self.assertLessEqual(translator.calls, 1 + 3)
        self.assertTrue(all(item["translation_status"] == "source_fallback" for item in translated))
        with self.assertRaises(TranslationIncompleteError) as raised:
            ensure_translation_complete(translated)
        self.assertIn("không phản hồi sau 60 giây", str(raised.exception))


class MissingOllamaModelTranslator:
    """What a local model whose Ollama tag is missing does: 404 on every request."""

    name = "translategemma-translategemma"
    supports_markers = False

    def __init__(self) -> None:
        self.calls = 0

    def translate(self, text):
        from infrastructure.vietnamese_translator import TranslationConfigurationError

        self.calls += 1
        raise TranslationConfigurationError("Ollama chưa có model translategemma; hãy chạy: ollama pull translategemma")


class WorkingTranslator:
    name = "google-zh-CN-vi"
    supports_markers = False

    def translate(self, text):
        return f"Câu dịch {text[1:-2]}"


class BrokenProviderFallbackTests(unittest.TestCase):
    def test_broken_local_model_falls_back_for_every_cue(self) -> None:
        # Regression: job 4fb6af20 failed 149/149 cues because one provider's model was missing.
        from infrastructure.translation_providers import FallbackTranslator
        from pipeline.translation import ensure_translation_complete

        cues = [{"start": float(i), "end": i + 1.0, "text": f"第{i}句话"} for i in range(149)]
        broken = MissingOllamaModelTranslator()
        translated = translate_segments(cues, FallbackTranslator(broken, WorkingTranslator()), retries=3,
                                        sleeper=lambda _: None, source_language="zh-CN")

        ensure_translation_complete(translated)
        self.assertEqual(broken.calls, 1)  # Marked down after the first 404, not retried per cue.
        self.assertTrue(all(item["translation_status"] == "translated" for item in translated))
