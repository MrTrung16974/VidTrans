from __future__ import annotations

import json
import io
import os
import re
import unittest
import urllib.error
from unittest.mock import patch

from infrastructure.translation_providers import (
    FallbackTranslator,
    GeminiContextTranslator,
    Qwen3Translator,
    TranslateGemmaTranslator,
    create_translation_provider,
    normalize_translation_provider,
    translategemma_available,
    translation_provider_statuses,
)
from infrastructure.vietnamese_translator import TranslationRateLimitedError, TranslationServiceError


class _Response:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, _limit: int) -> bytes:
        return self.payload


class TranslationProviderTests(unittest.TestCase):
    def test_normalizes_supported_provider(self) -> None:
        self.assertEqual(normalize_translation_provider(" Gemini "), "gemini")
        with self.assertRaisesRegex(ValueError, "translation_provider"):
            normalize_translation_provider("unknown")

    def test_gemini_preserves_line_count_and_order(self) -> None:
        response = {
            "candidates": [{"content": {"parts": [{"text": '["Xin chào", "Hẹn gặp lại"]'}]}}]
        }
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            translator = GeminiContextTranslator(
                source_language="zh-CN",
                target_language="vi",
                opener=lambda *_args, **_kwargs: _Response(response),
            )
            self.assertEqual(
                translator.translate_lines(["你好", "再见"]),
                ["Xin chào", "Hẹn gặp lại"],
            )

    def test_gemini_timeout_and_server_errors_are_unavailable(self) -> None:
        import urllib.error

        from infrastructure.vietnamese_translator import TranslationUnavailableError

        failures = [
            TimeoutError("timed out"),
            urllib.error.URLError(OSError("network unreachable")),
            urllib.error.HTTPError("https://x", 503, "unavailable", {}, None),
        ]
        for failure in failures:
            def opener(*_args, **_kwargs):
                raise failure

            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
                translator = GeminiContextTranslator(source_language="zh-CN", target_language="vi", opener=opener)
            with self.assertRaises(TranslationUnavailableError):
                translator.translate_lines(["你好"])

    def test_status_hides_unconfigured_gemini(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            statuses = {status.key: status for status in translation_provider_statuses()}
        self.assertTrue(statuses["google"].configured)
        self.assertFalse(statuses["gemini"].configured)

    def test_gemini_replaces_unavailable_configured_model(self) -> None:
        translated = {"candidates": [{"content": {"parts": [{"text": '["Xin chào"]'}]}}]}
        models = {"models": [{
            "name": "models/gemini-3.5-flash-lite",
            "supportedGenerationMethods": ["generateContent"],
        }]}
        calls: list[str] = []

        def opener(request, **_kwargs):
            calls.append(request.full_url)
            if "/models?" in request.full_url:
                return _Response(models)
            if "gemini-2.5-flash:generateContent" in request.full_url:
                raise urllib.error.HTTPError(
                    request.full_url, 404, "Not Found", {}, io.BytesIO(b'{"error":"model_not_found"}')
                )
            return _Response(translated)

        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "test-key",
            "GEMINI_TRANSLATION_MODEL": "gemini-2.5-flash",
        }, clear=True):
            translator = GeminiContextTranslator(
                source_language="zh-CN", target_language="vi", opener=opener
            )
            self.assertEqual(translator.translate_lines(["你好"]), ["Xin chào"])
        self.assertEqual(translator.model, "gemini-3.5-flash-lite")
        self.assertEqual(len(calls), 3)



class _FakeTranslator:
    name = "google"
    supports_markers = False

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = 0

    def translate(self, text: str) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return f"primary:{text}"

    def translate_lines(self, lines: list[str]) -> list[str]:
        return [self.translate(line) for line in lines]


class _FakeSecondary:
    name = "translategemma-translategemma"

    def translate(self, text: str) -> str:
        return f"local:{text}"


class TranslateGemmaTests(unittest.TestCase):
    def test_sends_official_prompt_and_returns_ollama_message(self) -> None:
        sent = {}

        def opener(request, **_kwargs):
            sent["url"] = request.full_url
            sent["body"] = json.loads(request.data.decode("utf-8"))
            return _Response({"message": {"role": "assistant", "content": " Em yêu anh \n"}})

        translator = TranslateGemmaTranslator(source_language="zh-CN", target_language="vi", opener=opener)

        self.assertEqual(translator.translate("我愛你"), "Em yêu anh")
        self.assertTrue(sent["url"].endswith("/api/chat"))
        prompt = sent["body"]["messages"][0]["content"]
        self.assertIn("Chinese (zh-TW) to Vietnamese (vi) translator", prompt)
        self.assertTrue(prompt.endswith("\n\n\n我愛你"))
        self.assertEqual(sent["body"]["options"]["temperature"], 0)
        self.assertEqual(sent["body"]["options"]["num_ctx"], 2048)
        self.assertEqual(sent["body"]["keep_alive"], "30s")

    def test_missing_model_is_a_permanent_error(self) -> None:
        def opener(request, **_kwargs):
            raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, io.BytesIO(b""))

        translator = TranslateGemmaTranslator(source_language="zh-CN", target_language="vi", opener=opener)
        with self.assertRaises(TranslationServiceError) as raised:
            translator.translate("你好")
        self.assertTrue(getattr(raised.exception, "permanent", False))

    def test_availability_requires_the_model_in_ollama(self) -> None:
        tags = {"models": [{"name": "translategemma:latest"}]}
        self.assertTrue(translategemma_available(opener=lambda *_a, **_k: _Response(tags), refresh=True))
        other = {"models": [{"name": "llama3:latest"}]}
        self.assertFalse(translategemma_available(opener=lambda *_a, **_k: _Response(other), refresh=True))

    def test_unreachable_ollama_is_not_available(self) -> None:
        def opener(*_args, **_kwargs):
            raise urllib.error.URLError("refused")

        self.assertFalse(translategemma_available(opener=opener, refresh=True))

    def test_cloud_provider_gets_local_backup_only_when_ollama_is_ready(self) -> None:
        with patch("infrastructure.translation_providers.local_model_available", return_value=True):
            wrapped = create_translation_provider("google", source_language="zh-CN", target_language="vi")
            local = create_translation_provider("translategemma", source_language="zh-CN", target_language="vi")
        with patch("infrastructure.translation_providers.local_model_available", return_value=False):
            plain = create_translation_provider("google", source_language="zh-CN", target_language="vi")

        self.assertIsInstance(wrapped, FallbackTranslator)
        # A chosen local model is backed by Google, then the other local model.
        self.assertIsInstance(local.primary, TranslateGemmaTranslator)
        self.assertEqual(local.secondary.primary.name, "google-zh-CN-vi")
        self.assertIsInstance(local.secondary.secondary, Qwen3Translator)
        self.assertNotIsInstance(plain, FallbackTranslator)


class Qwen3Tests(unittest.TestCase):
    def test_translates_batch_with_context_and_thinking_disabled(self) -> None:
        sent = {}

        def opener(request, **_kwargs):
            sent["body"] = json.loads(request.data.decode("utf-8"))
            content = '<think></think>{"translations": ["Vốn dĩ rất yêu anh", "Em không nên thấy"]}'
            return _Response({"message": {"role": "assistant", "content": content}})

        translator = Qwen3Translator(source_language="zh-CN", target_language="vi", opener=opener)

        self.assertEqual(
            translator.translate_lines(["本來是挺愛你的", "我不該看到"]),
            ["Vốn dĩ rất yêu anh", "Em không nên thấy"],
        )
        self.assertEqual(sent["body"]["model"], "qwen3:4b")
        self.assertIs(sent["body"]["think"], False)
        self.assertEqual(sent["body"]["options"]["num_ctx"], 8192)
        self.assertEqual(sent["body"]["keep_alive"], "30s")
        system, user = sent["body"]["messages"]
        self.assertEqual(system["role"], "system")
        self.assertIn("Traditional Chinese to Vietnamese", system["content"])
        self.assertIn("本來是挺愛你的", user["content"])

    def test_instruct_model_without_thinking_support_is_retried_without_flag(self) -> None:
        bodies = []

        def opener(request, **_kwargs):
            body = json.loads(request.data.decode("utf-8"))
            bodies.append(body)
            if "think" in body:
                raise urllib.error.HTTPError(request.full_url, 400, "does not support thinking", {}, io.BytesIO(b""))
            return _Response({"message": {"content": '{"translations": ["Xin chào"]}'}})

        translator = Qwen3Translator(source_language="zh-CN", target_language="vi", opener=opener)
        self.assertEqual(translator.translate("你好"), "Xin chào")
        self.assertEqual(len(bodies), 2)

    def test_wrong_line_count_is_retried_in_halves(self) -> None:
        opener = lambda *_a, **_k: _Response({"message": {"content": '{"translations": ["một"]}'}})
        translator = Qwen3Translator(source_language="zh-CN", target_language="vi", opener=opener)
        self.assertEqual(translator.translate_lines(["一", "二"]), ["một", "một"])

    def test_single_line_with_unusable_output_is_rejected(self) -> None:
        opener = lambda *_a, **_k: _Response({"message": {"content": '{"translations": []}'}})
        translator = Qwen3Translator(source_language="zh-CN", target_language="vi", opener=opener)
        with self.assertRaisesRegex(TranslationServiceError, "count_mismatch"):
            translator.translate_lines(["一"])

    def test_source_script_left_by_the_model_is_rescued_by_google(self) -> None:
        reply = '{"translations": [{"id": 1, "text": "Đây là 小米"}]}'
        opener = lambda *_a, **_k: _Response({"message": {"content": reply}})

        class Rescue:
            name = "google"
            calls: list[str] = []

            def translate(self, text):
                self.calls.append(text)
                return "Đây là Xiaomi"

        rescue = Rescue()
        translator = Qwen3Translator(source_language="zh-CN", target_language="vi", opener=opener, rescue=rescue)
        self.assertEqual(translator.translate("这是小米"), "Đây là Xiaomi")
        self.assertEqual(rescue.calls, ["这是小米"])

    def test_failed_rescue_still_reports_untranslated_source(self) -> None:
        reply = '{"translations": [{"id": 1, "text": "Đây là 小米"}]}'
        opener = lambda *_a, **_k: _Response({"message": {"content": reply}})

        class Rescue:
            def translate(self, text):
                raise OSError("offline")

        translator = Qwen3Translator(source_language="zh-CN", target_language="vi", opener=opener, rescue=Rescue())
        with self.assertRaisesRegex(TranslationServiceError, "qwen3_untranslated_source_text"):
            translator.translate("这是小米")

    def test_batch_timeout_is_not_treated_as_provider_down(self) -> None:
        def opener(request, **_kwargs):
            raise TimeoutError()

        translator = Qwen3Translator(source_language="zh-CN", target_language="vi", opener=opener)
        with self.assertRaises(TranslationServiceError) as single:
            translator.translate("一")
        self.assertTrue(getattr(single.exception, "unavailable", False))

    def test_long_batches_are_sent_in_small_chunks_that_shrink_after_a_timeout(self) -> None:
        sizes: list[int] = []

        def opener(request, **_kwargs):
            body = json.loads(request.data.decode("utf-8"))
            count = int(re.search(r"Translate these (\d+) items", body["messages"][1]["content"]).group(1))
            sizes.append(count)
            if count > 3:
                raise TimeoutError()
            return _Response({"message": {"content": json.dumps(
                {"translations": [{"id": i + 1, "text": f"câu {i}"} for i in range(count)]}
            )}})

        with patch.dict(os.environ, {"VIDTRANS_QWEN3_BATCH_LINES": "6"}):
            translator = Qwen3Translator(source_language="zh-CN", target_language="vi", opener=opener)
        lines = [f"句子{i}" for i in range(14)]
        self.assertEqual(len(translator.translate_lines(lines)), 14)
        self.assertEqual(sizes[0], 6)
        self.assertEqual(translator.chunk_lines, 3)
        self.assertTrue(all(size <= 3 for size in sizes[1:]))

    def test_model_tag_must_match_exactly(self) -> None:
        tags = {"models": [{"name": "qwen3:8b"}]}
        from infrastructure.translation_providers import local_model_available
        self.assertFalse(local_model_available("qwen3", opener=lambda *_a, **_k: _Response(tags), refresh=True))
        tags = {"models": [{"name": "qwen3:4b"}]}
        self.assertTrue(local_model_available("qwen3", opener=lambda *_a, **_k: _Response(tags), refresh=True))

    def test_qwen3_is_preferred_backup(self) -> None:
        with patch("infrastructure.translation_providers.local_model_available", return_value=True):
            wrapped = create_translation_provider("google", source_language="zh-CN", target_language="vi")
        self.assertIsInstance(wrapped.secondary.primary, Qwen3Translator)
        self.assertIsInstance(wrapped.secondary.secondary, TranslateGemmaTranslator)

    def test_untagged_model_name_requests_the_installed_tag(self) -> None:
        # Ollama resolves "translategemma" to ":latest" and answers 404 when only ":4b" exists.
        from infrastructure.translation_providers import installed_local_model

        tags = lambda names: (lambda *_a, **_k: _Response({"models": [{"name": n} for n in names]}))
        with patch("infrastructure.translation_providers._ollama_models", None):
            self.assertEqual(installed_local_model("translategemma", opener=tags(["translategemma:4b"])),
                             "translategemma:4b")
        with patch("infrastructure.translation_providers._ollama_models", None):
            self.assertEqual(installed_local_model("translategemma",
                                                   opener=tags(["translategemma:12b", "translategemma:latest"])),
                             "translategemma")
        sent = {}

        def opener(request, **_kwargs):
            if request.full_url.endswith("/api/tags"):
                return _Response({"models": [{"name": "translategemma:4b"}]})
            sent["model"] = json.loads(request.data.decode("utf-8"))["model"]
            return _Response({"message": {"content": "Xin chào"}})

        with patch("infrastructure.translation_providers._ollama_models", None):
            translator = TranslateGemmaTranslator(source_language="zh-CN", target_language="vi", opener=opener)
            self.assertEqual(translator.translate("你好"), "Xin chào")
        self.assertEqual(sent["model"], "translategemma:4b")


class SubtitlePromptTests(unittest.TestCase):
    @staticmethod
    def qwen(replies: list[str], sent: list[dict]) -> Qwen3Translator:
        def opener(request, **_kwargs):
            sent.append(json.loads(request.data.decode("utf-8")))
            return _Response({"message": {"content": replies.pop(0)}})

        # The rescue echoes the source, so a leak survives it and no real web request is made.
        echo = type("Echo", (), {"translate": staticmethod(lambda text: text)})()
        return Qwen3Translator(source_language="zh-CN", target_language="vi", opener=opener, rescue=echo)

    def test_simplified_chinese_keeps_zh_cn_code(self) -> None:
        sent = {}

        def opener(request, **_kwargs):
            sent["body"] = json.loads(request.data.decode("utf-8"))
            return _Response({"message": {"content": "Em yêu anh"}})

        TranslateGemmaTranslator(source_language="zh-CN", target_language="vi", opener=opener).translate("我爱你")
        self.assertIn("Chinese (zh-CN)", sent["body"]["messages"][0]["content"])

    def test_durations_and_previous_lines_are_sent(self) -> None:
        sent: list[dict] = []
        translator = self.qwen(
            ['{"translations": [{"id": 1, "text": "Một"}]}', '{"translations": [{"id": 1, "text": "Hai"}]}'],
            sent,
        )
        translator.translate_lines(["一"], durations=[1.84])
        translator.translate_lines(["二"], durations=[2.0])

        first_user = sent[0]["messages"][1]["content"]
        second_user = sent[1]["messages"][1]["content"]
        self.assertIn('"seconds": 1.8', first_user)
        self.assertNotIn("Previous lines", first_user)
        self.assertIn('"source": "一", "translation": "Một"', second_user)

    def test_ids_are_reordered_and_checked(self) -> None:
        sent: list[dict] = []
        translator = self.qwen(['{"translations": [{"id": 2, "text": "Hai"}, {"id": 1, "text": "Một"}]}'], sent)
        self.assertEqual(translator.translate_lines(["一", "二"]), ["Một", "Hai"])

    def test_leftover_chinese_is_retried_alone(self) -> None:
        sent: list[dict] = []
        translator = self.qwen(
            [
                '{"translations": [{"id": 1, "text": "Xin chào"}, {"id": 2, "text": "Quán này 绝绝子"}]}',
                '{"translations": [{"id": 1, "text": "Quán này tuyệt vời"}]}',
            ],
            sent,
        )
        self.assertEqual(translator.translate_lines(["你好", "这家店绝绝子"]), ["Xin chào", "Quán này tuyệt vời"])
        self.assertEqual(len(sent), 2)

    def test_chinese_left_after_retry_fails_the_batch(self) -> None:
        sent: list[dict] = []
        translator = self.qwen(
            ['{"translations": [{"id": 1, "text": "绝绝子"}]}', '{"translations": [{"id": 1, "text": "绝绝子"}]}'],
            sent,
        )
        with self.assertRaisesRegex(TranslationServiceError, "untranslated"):
            translator.translate_lines(["绝绝子"])

    def test_gemini_uses_system_instruction_and_object_output(self) -> None:
        sent = {}

        def opener(request, **_kwargs):
            sent["body"] = json.loads(request.data.decode("utf-8"))
            text = '{"translations": [{"id": 1, "text": "Xin chào"}]}'
            return _Response({"candidates": [{"content": {"parts": [{"text": text}]}}]})

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            translator = GeminiContextTranslator(source_language="zh-CN", target_language="vi", opener=opener)
            self.assertEqual(translator.translate_lines(["你好"], durations=[1.0]), ["Xin chào"])
        self.assertIn("dubbing translator", sent["body"]["systemInstruction"]["parts"][0]["text"])


class FallbackTranslatorTests(unittest.TestCase):
    def test_uses_primary_while_it_works(self) -> None:
        translator = FallbackTranslator(_FakeTranslator(), _FakeSecondary())
        self.assertEqual(translator.translate("你好"), "primary:你好")
        self.assertEqual(translator.name, "google")

    def test_failed_cue_is_translated_by_secondary(self) -> None:
        translator = FallbackTranslator(_FakeTranslator(TranslationServiceError("boom")), _FakeSecondary())
        self.assertEqual(translator.translate("你好"), "local:你好")
        self.assertEqual(translator.name, "google+translategemma-translategemma")

    def test_rate_limited_primary_is_skipped_for_rest_of_job(self) -> None:
        primary = _FakeTranslator(TranslationRateLimitedError("429"))
        translator = FallbackTranslator(primary, _FakeSecondary())
        self.assertEqual(translator.translate_lines(["一", "二"]), ["local:一", "local:二"])
        self.assertEqual(translator.translate("三"), "local:三")
        self.assertEqual(primary.calls, 1)

    def test_broken_backup_does_not_block_a_primary_that_failed_one_cue(self) -> None:
        from infrastructure.vietnamese_translator import TranslationConfigurationError

        class BrokenSecondary(_FakeSecondary):
            def translate(self, text):
                raise TranslationConfigurationError("Ollama chưa có model translategemma")

        translator = FallbackTranslator(_FakeTranslator(TranslationServiceError("timeout")), BrokenSecondary())
        with self.assertRaises(TranslationServiceError) as raised:
            translator.translate("你好")
        self.assertIn("timeout", str(raised.exception))
        self.assertIn("translategemma", str(raised.exception))
        self.assertFalse(getattr(raised.exception, "permanent", False))

    def test_job_stops_only_when_every_translator_is_blocked(self) -> None:
        from infrastructure.vietnamese_translator import TranslationConfigurationError

        class BrokenSecondary(_FakeSecondary):
            def translate(self, text):
                raise TranslationConfigurationError("no model")

        translator = FallbackTranslator(_FakeTranslator(TranslationRateLimitedError("429")), BrokenSecondary())
        with self.assertRaises(TranslationServiceError) as raised:
            translator.translate("你好")
        self.assertTrue(raised.exception.permanent)

    def test_marker_batches_are_not_sent_to_secondary(self) -> None:
        translator = FallbackTranslator(_FakeTranslator(TranslationServiceError("boom")), _FakeSecondary())
        with self.assertRaises(TranslationServiceError):
            translator.translate('<vts id="000001"/>\n你好')


if __name__ == "__main__":
    unittest.main()


# Lines 19-23 of job 05a05e62: Gemini skipped the song lyric and shifted every translation up.
JOB_05A05E62_SOURCES = ["不要生情来只编织自己的爱", "关别人是说好还是说坏", "你我也不是天才", "只是很怕你的坏小孩", "肯定是亲生的"]
JOB_05A05E62_TRANSLATIONS = ["Đừng sống vì ánh mắt người khác", "Đừng bận tâm người ta nói tốt hay xấu",
                             "Cả anh và em đều chẳng phải thiên tài", "Chỉ là đứa trẻ sợ mất em thôi",
                             "Chắc chắn là con ruột rồi"]


def _gemini_reply(items: list[dict]) -> _Response:
    text = json.dumps({"translations": items}, ensure_ascii=False)
    return _Response({"candidates": [{"content": {"parts": [{"text": text}]}}]})


class MisalignedBatchTests(unittest.TestCase):
    def test_shifted_echo_is_rejected(self) -> None:
        from infrastructure.translation_prompt import TranslationOutputError, parse_translations

        shifted = [{"id": i + 1, "source": JOB_05A05E62_SOURCES[i + 1], "text": JOB_05A05E62_TRANSLATIONS[i + 1]}
                   for i in range(4)]
        shifted.append({"id": 5, "source": JOB_05A05E62_SOURCES[4], "text": JOB_05A05E62_TRANSLATIONS[4]})
        with self.assertRaisesRegex(TranslationOutputError, "misaligned"):
            parse_translations(json.dumps({"translations": shifted}), 5, provider="gemini",
                               sources=JOB_05A05E62_SOURCES)

    def test_repeat_padding_without_echo_is_rejected(self) -> None:
        from infrastructure.translation_prompt import TranslationOutputError, parse_translations

        padded = [*JOB_05A05E62_TRANSLATIONS[1:], JOB_05A05E62_TRANSLATIONS[4]]
        with self.assertRaisesRegex(TranslationOutputError, "misaligned"):
            parse_translations(json.dumps(padded), 5, provider="gemini", sources=JOB_05A05E62_SOURCES)

    def test_echo_tolerates_script_and_punctuation_and_real_repeats(self) -> None:
        from infrastructure.translation_prompt import parse_translations

        items = [{"id": 1, "source": "這個 怎麼了？", "text": "Sao thế"},
                 {"id": 2, "source": "浩浩", "text": "Hạo Hạo"}, {"id": 3, "source": "浩浩浩浩", "text": "Hạo Hạo"}]
        self.assertEqual(
            parse_translations(json.dumps({"translations": items}, ensure_ascii=False), 3, provider="gemini",
                               sources=["这个怎么了", "浩浩", "浩浩浩浩"]),
            ["Sao thế", "Hạo Hạo", "Hạo Hạo"],
        )

    def test_gemini_retries_a_shifted_batch_in_halves_instead_of_using_google(self) -> None:
        requests: list[list[str]] = []

        def opener(request, **_kwargs):
            sent = json.loads(json.loads(request.data)["contents"][0]["parts"][0]["text"].split("items: ", 1)[1])
            lines = [item["text"] for item in sent]
            requests.append(lines)
            if len(lines) == 5:
                return _gemini_reply([{"id": i + 1, "source": JOB_05A05E62_SOURCES[min(i + 1, 4)],
                                       "text": JOB_05A05E62_TRANSLATIONS[min(i + 1, 4)]} for i in range(5)])
            return _gemini_reply([{"id": i + 1, "source": line,
                                   "text": JOB_05A05E62_TRANSLATIONS[JOB_05A05E62_SOURCES.index(line)]}
                                  for i, line in enumerate(lines)])

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            translator = GeminiContextTranslator(source_language="zh-CN", target_language="vi", opener=opener)
        self.assertEqual(translator.translate_lines(list(JOB_05A05E62_SOURCES)), JOB_05A05E62_TRANSLATIONS)
        self.assertEqual([len(lines) for lines in requests], [5, 2, 3])


class FallbackReportingTests(unittest.TestCase):
    def test_cues_translated_by_the_backup_are_reported(self) -> None:
        from infrastructure.translation_prompt import TranslationOutputError
        from infrastructure.translation_providers import FallbackTranslator
        from pipeline.translation import translate_segments

        class Primary:
            name = "gemini-test"
            supports_markers = False

            def translate_lines(self, lines, durations=None):
                if "坏" in "".join(lines):
                    raise TranslationOutputError("gemini_translation_misaligned")
                return [f"vi:{line}" for line in lines]

            def translate(self, text):
                return self.translate_lines([text])[0]

        class Backup:
            name = "google"

            def translate(self, text):
                return f"gg:{text}"

        translator = FallbackTranslator(Primary(), Backup())
        segments = [{"start": float(i), "end": float(i) + 1, "text": text}
                    for i, text in enumerate(["你好", "坏人", "谢谢"])]
        output = translate_segments(segments, translator, source_language="zh-CN", max_batch_chars=200,
                                    sleeper=lambda _seconds: None)
        self.assertEqual([cue["translation_fallback"] for cue in output], [True, True, True])
        self.assertEqual({cue["translation_provider"] for cue in output}, {"google"})

        single = translate_segments(segments[:1], FallbackTranslator(Primary(), Backup()),
                                    source_language="zh-CN", max_batch_chars=200, sleeper=lambda _seconds: None)
        self.assertFalse(single[0]["translation_fallback"])
        self.assertEqual(single[0]["translation_provider"], "gemini-test")
