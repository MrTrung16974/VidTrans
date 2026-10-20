from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from domain.languages import language_profile, validate_language_pair
from infrastructure.vietnamese_translator import (
    GoogleVietnameseTranslator,
    TranslationRateLimitedError,
    TranslationServiceError,
)


SUPPORTED_TRANSLATION_PROVIDERS = frozenset({"google", "gemini"})


def normalize_translation_provider(value: str | None) -> str:
    provider = str(value or "google").strip().lower()
    if provider not in SUPPORTED_TRANSLATION_PROVIDERS:
        raise ValueError("translation_provider must be google or gemini")
    return provider


def ensure_translation_provider_configured(provider: str) -> None:
    if normalize_translation_provider(provider) == "gemini" and not os.environ.get("GEMINI_API_KEY", "").strip():
        raise ValueError("Gemini chưa được cấu hình. Hãy đặt GEMINI_API_KEY trên máy chủ")


@dataclass(frozen=True)
class TranslationProviderStatus:
    key: str
    label: str
    configured: bool
    free_tier: bool
    description: str


def translation_provider_statuses() -> list[TranslationProviderStatus]:
    return [
        TranslationProviderStatus(
            "google", "Google Translate", True, True,
            "Bộ dịch ban đầu của VidTrans: nhanh, miễn phí và phù hợp dịch thông thường",
        ),
        TranslationProviderStatus(
            "gemini", "Gemini AI", bool(os.environ.get("GEMINI_API_KEY", "").strip()), True,
            "Dịch theo ngữ cảnh, câu tiếng Việt tự nhiên hơn; dùng hạn mức Gemini API Free",
        ),
    ]


class GeminiContextTranslator:
    supports_markers = False

    def __init__(self, *, source_language: str, target_language: str, timeout: float = 60.0, opener=None):
        self.source_language, self.target_language = validate_language_pair(source_language, target_language)
        if self.source_language == "auto":
            raise TranslationServiceError("translator requires a detected source language")
        self.api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not self.api_key:
            raise TranslationServiceError("gemini_api_key_missing")
        self.model = os.environ.get("GEMINI_TRANSLATION_MODEL", "gemini-2.5-flash").strip()
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen
        self.name = f"gemini-{self.model}"

    @staticmethod
    def _extract_json(text: str) -> list[str]:
        value = text.strip()
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
        payload = json.loads(value)
        if not isinstance(payload, list) or not all(isinstance(item, str) and item.strip() for item in payload):
            raise TranslationServiceError("gemini_invalid_translation_array")
        return [item.strip() for item in payload]

    def translate_lines(self, lines: list[str]) -> list[str]:
        source_label = language_profile(self.source_language).label_vi
        target_label = language_profile(self.target_language).label_vi
        prompt = (
            f"Bạn là biên dịch viên phụ đề video. Dịch mảng câu từ {source_label} sang {target_label}. "
            "Dựa vào các câu liền nhau để hiểu ngữ cảnh, đại từ và cách xưng hô. Dịch sát nội dung nhưng "
            "diễn đạt tự nhiên, ngắn gọn để đọc thành tiếng; không giải thích, không thêm ý. "
            "Giữ đúng số phần tử và chỉ trả về một JSON array chuỗi theo đúng thứ tự.\n"
            + json.dumps(lines, ensure_ascii=False)
        )
        body = json.dumps({
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }).encode("utf-8")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            translated = self._extract_json(text)
            if len(translated) != len(lines):
                raise TranslationServiceError("gemini_translation_count_mismatch")
            return translated
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise TranslationRateLimitedError("Gemini đã hết hạn mức tạm thời (HTTP 429)") from exc
            raise TranslationServiceError(f"Gemini API lỗi HTTP {exc.code}") from exc
        except TranslationServiceError:
            raise
        except Exception as exc:
            raise TranslationServiceError(f"Gemini API lỗi: {type(exc).__name__}") from exc

    def translate(self, text: str) -> str:
        return self.translate_lines([text])[0]


def create_translation_provider(provider: str, *, source_language: str, target_language: str):
    resolved = normalize_translation_provider(provider)
    if resolved == "gemini":
        return GeminiContextTranslator(source_language=source_language, target_language=target_language)
    return GoogleVietnameseTranslator(source_language=source_language, target_language=target_language)
