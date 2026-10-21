from __future__ import annotations

import json
import io
import os
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
        self.assertIsInstance(local, TranslateGemmaTranslator)
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

    def test_wrong_line_count_is_rejected(self) -> None:
        opener = lambda *_a, **_k: _Response({"message": {"content": '{"translations": ["một"]}'}})
        translator = Qwen3Translator(source_language="zh-CN", target_language="vi", opener=opener)
        with self.assertRaisesRegex(TranslationServiceError, "count_mismatch"):
            translator.translate_lines(["一", "二"])

    def test_model_tag_must_match_exactly(self) -> None:
        tags = {"models": [{"name": "qwen3:8b"}]}
        from infrastructure.translation_providers import local_model_available
        self.assertFalse(local_model_available("qwen3", opener=lambda *_a, **_k: _Response(tags), refresh=True))
        tags = {"models": [{"name": "qwen3:4b"}]}
        self.assertTrue(local_model_available("qwen3", opener=lambda *_a, **_k: _Response(tags), refresh=True))

    def test_qwen3_is_preferred_backup(self) -> None:
        with patch("infrastructure.translation_providers.local_model_available", return_value=True):
            wrapped = create_translation_provider("google", source_language="zh-CN", target_language="vi")
        self.assertIsInstance(wrapped.secondary, Qwen3Translator)


class SubtitlePromptTests(unittest.TestCase):
    @staticmethod
    def qwen(replies: list[str], sent: list[dict]) -> Qwen3Translator:
        def opener(request, **_kwargs):
            sent.append(json.loads(request.data.decode("utf-8")))
            return _Response({"message": {"content": replies.pop(0)}})

        return Qwen3Translator(source_language="zh-CN", target_language="vi", opener=opener)

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

    def test_marker_batches_are_not_sent_to_secondary(self) -> None:
        translator = FallbackTranslator(_FakeTranslator(TranslationServiceError("boom")), _FakeSecondary())
        with self.assertRaises(TranslationServiceError):
            translator.translate('<vts id="000001"/>\n你好')


if __name__ == "__main__":
    unittest.main()
