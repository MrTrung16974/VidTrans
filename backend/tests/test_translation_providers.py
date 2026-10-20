from __future__ import annotations

import json
import os
import unittest
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


if __name__ == "__main__":
    unittest.main()
