from __future__ import annotations

import json
import io
import os
import unittest
import urllib.error
from unittest.mock import patch

from infrastructure.translation_providers import (
    GeminiContextTranslator,
    normalize_translation_provider,
    translation_provider_statuses,
)


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


if __name__ == "__main__":
    unittest.main()
