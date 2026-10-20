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
    TranslationConfigurationError,
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
        self.model = os.environ.get("GEMINI_TRANSLATION_MODEL", "gemini-3.5-flash-lite").strip()
        configured_models = [
            value.strip()
            for value in os.environ.get("GEMINI_TRANSLATION_MODELS", "").split(",")
            if value.strip()
        ]
        self.model_candidates = list(dict.fromkeys([
            self.model,
            *configured_models,
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.8-flash",
            "gemini-3.6-flash",
            "gemini-3-flash-preview",
            "gemini-2.5-flash-lite",
            "gemini-2.5-flash",
        ]))
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen
        self.name = f"gemini-{self.model}"

    def _available_model_candidates(self, *, exclude: set[str]) -> list[str]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}&pageSize=1000"
        request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with self._open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
        except Exception:
            # Listing can itself be temporarily limited. Known model IDs are
            # still safe to try once; generateContent decides actual access.
            return [model for model in self.model_candidates if model not in exclude]
        available = {
            str(item.get("name", "")).removeprefix("models/")
            for item in payload.get("models", [])
            if "generateContent" in item.get("supportedGenerationMethods", [])
        }
        candidates = [
            model for model in self.model_candidates
            if model in available and model not in exclude
        ]
        if candidates:
            return candidates
        raise TranslationConfigurationError(
            "API key không còn model Gemini khả dụng hỗ trợ generateContent"
        )

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
        def generate(model: str):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
            request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            with self._open(request, timeout=self.timeout) as response:
                return json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))

        try:
            queue = [self.model]
            attempted: set[str] = set()
            limited: list[str] = []
            not_found: list[str] = []
            expanded = False
            payload = None
            while queue:
                candidate = queue.pop(0)
                if candidate in attempted:
                    continue
                attempted.add(candidate)
                try:
                    payload = generate(candidate)
                    self.model = candidate
                    self.name = f"gemini-{candidate}"
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code == 429:
                        limited.append(candidate)
                    elif exc.code == 404:
                        not_found.append(candidate)
                    else:
                        raise
                    if not expanded:
                        queue.extend(self._available_model_candidates(exclude=attempted))
                        expanded = True
            if payload is None:
                attempted_label = ", ".join(attempted)
                if limited:
                    raise TranslationRateLimitedError(
                        f"Các model Gemini đã hết hạn mức tạm thời: {attempted_label}"
                    )
                raise TranslationConfigurationError(
                    f"Không tìm thấy model Gemini khả dụng: {attempted_label}"
                )
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            translated = self._extract_json(text)
            if len(translated) != len(lines):
                raise TranslationServiceError("gemini_translation_count_mismatch")
            return translated
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise TranslationRateLimitedError("Gemini đã hết hạn mức tạm thời (HTTP 429)") from exc
            if exc.code == 404:
                raise TranslationConfigurationError(f"Gemini không tìm thấy model {self.model}") from exc
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
