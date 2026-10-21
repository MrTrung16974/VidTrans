from __future__ import annotations

import json
import os
import re
import time
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


SUPPORTED_TRANSLATION_PROVIDERS = frozenset({"google", "gemini", "translategemma", "qwen3"})

# Local models run through Ollama on this machine (`ollama pull <model>`).
DEFAULT_OLLAMA_URL = "http://localhost:11434"
OLLAMA_STATUS_TTL_SECONDS = 30.0
# provider -> (model env var, default model, enable env var)
LOCAL_TRANSLATION_MODELS = {
    "translategemma": ("VIDTRANS_TRANSLATEGEMMA_MODEL", "translategemma", "VIDTRANS_TRANSLATEGEMMA_ENABLED"),
    "qwen3": ("VIDTRANS_QWEN3_MODEL", "qwen3:4b", "VIDTRANS_QWEN3_ENABLED"),
}
# Qwen3 reads neighbouring lines (consistent pronouns), so it is the preferred backup.
LOCAL_BACKUP_ORDER = ("qwen3", "translategemma")
# The official TranslateGemma prompt expects English language names.
TRANSLATEGEMMA_LANGUAGE_NAMES = {
    "vi": "Vietnamese", "en": "English", "zh-CN": "Chinese", "ja": "Japanese",
    "ko": "Korean", "th": "Thai", "id": "Indonesian", "es": "Spanish",
}


def normalize_translation_provider(value: str | None) -> str:
    provider = str(value or "google").strip().lower()
    if provider not in SUPPORTED_TRANSLATION_PROVIDERS:
        raise ValueError("translation_provider must be google, gemini, translategemma or qwen3")
    return provider


def ensure_translation_provider_configured(provider: str) -> None:
    resolved = normalize_translation_provider(provider)
    if resolved == "gemini" and not os.environ.get("GEMINI_API_KEY", "").strip():
        raise ValueError("Gemini chưa được cấu hình. Hãy đặt GEMINI_API_KEY trên máy chủ")
    if resolved in LOCAL_TRANSLATION_MODELS and not local_model_available(resolved):
        raise ValueError(
            f"{resolved} chưa sẵn sàng. Hãy chạy Ollama và tải model: ollama pull {local_model(resolved)}"
        )


def ollama_url() -> str:
    value = os.environ.get("VIDTRANS_OLLAMA_URL") or os.environ.get("VIDTRANS_TRANSLATEGEMMA_URL") or DEFAULT_OLLAMA_URL
    return value.strip().rstrip("/")


def local_model(provider: str) -> str:
    model_env, default_model, _enabled_env = LOCAL_TRANSLATION_MODELS[provider]
    return os.environ.get(model_env, default_model).strip()


def translategemma_model() -> str:
    return local_model("translategemma")


_ollama_models: tuple[float, frozenset[str]] | None = None
# Ollama is local: never route it through HTTP(S)_PROXY or the system proxy.
_direct_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


def _installed_ollama_models(*, opener=None, refresh: bool = False) -> frozenset[str]:
    global _ollama_models
    now = time.monotonic()
    if not refresh and _ollama_models and now - _ollama_models[0] < OLLAMA_STATUS_TTL_SECONDS:
        return _ollama_models[1]
    request = urllib.request.Request(f"{ollama_url()}/api/tags", headers={"Accept": "application/json"})
    try:
        with (opener or _direct_open)(request, timeout=1.5) as response:
            payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
        names = frozenset(str(item.get("name", "")) for item in payload.get("models", []))
    except Exception:
        names = frozenset()
    _ollama_models = (now, names)
    return names


def local_model_available(provider: str, *, opener=None, refresh: bool = False) -> bool:
    """Whether the local Ollama server answers and has this provider's model."""

    _model_env, _default, enabled_env = LOCAL_TRANSLATION_MODELS[provider]
    if os.environ.get(enabled_env, "1").strip().lower() in {"0", "false", "no"}:
        return False
    wanted = local_model(provider)
    names = _installed_ollama_models(opener=opener, refresh=refresh)
    if ":" in wanted:
        return wanted in names
    return any(name.split(":")[0] == wanted for name in names)


def translategemma_available(*, opener=None, refresh: bool = False) -> bool:
    return local_model_available("translategemma", opener=opener, refresh=refresh)


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
        TranslationProviderStatus(
            "translategemma", "TranslateGemma (local)", translategemma_available(), True,
            "AI dịch chạy trên máy qua Ollama: miễn phí, không giới hạn, không gửi dữ liệu ra ngoài. "
            "Khi sẵn sàng, nó cũng tự dịch thay những câu Google/Gemini bị lỗi",
        ),
        TranslationProviderStatus(
            "qwen3", "Qwen3-4B (local)", local_model_available("qwen3"), True,
            "AI local qua Ollama (~2.5–3 GB): dịch Trung–Việt tốt, đọc cả đoạn nên xưng hô nhất quán; "
            "cân bằng giữa chất lượng và tốc độ. Là bộ dịch phụ ưu tiên khi Google/Gemini lỗi",
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


class TranslateGemmaTranslator:
    """Local TranslateGemma model served by Ollama, one cue per request."""

    supports_markers = False

    def __init__(self, *, source_language: str, target_language: str, timeout: float = 120.0, opener=None):
        self.source_language, self.target_language = validate_language_pair(source_language, target_language)
        if self.source_language == "auto":
            raise TranslationServiceError("translator requires a detected source language")
        self.model = translategemma_model()
        self.url = ollama_url()
        self.timeout = timeout
        self._open = opener or _direct_open
        self.name = f"translategemma-{self.model}"

    def _prompt(self, text: str) -> str:
        source_code = language_profile(self.source_language).code
        target_code = language_profile(self.target_language).code
        source = TRANSLATEGEMMA_LANGUAGE_NAMES.get(source_code, source_code)
        target = TRANSLATEGEMMA_LANGUAGE_NAMES.get(target_code, target_code)
        return (
            f"You are a professional {source} ({source_code}) to {target} ({target_code}) translator. "
            f"Your goal is to accurately convey the meaning and nuances of the original {source} text "
            f"while adhering to {target} grammar, vocabulary, and cultural sensitivities.\n"
            f"Produce only the {target} translation, without any additional explanations or commentary. "
            f"Please translate the following {source} text into {target}:\n\n\n{text}"
        )

    def translate(self, text: str) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": self._prompt(text)}],
            "stream": False,
            "options": {"temperature": 0},
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with self._open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise TranslationConfigurationError(
                    f"Ollama chưa có model {self.model}; hãy chạy: ollama pull {self.model}"
                ) from exc
            raise TranslationServiceError(f"TranslateGemma lỗi HTTP {exc.code}") from exc
        except (urllib.error.URLError, ConnectionError) as exc:
            raise TranslationConfigurationError("Không kết nối được Ollama cho TranslateGemma") from exc
        except Exception as exc:
            raise TranslationServiceError(f"TranslateGemma lỗi: {type(exc).__name__}") from exc
        translated = str((payload.get("message") or {}).get("content") or "").strip()
        if not translated:
            raise TranslationServiceError("translategemma_empty_translation")
        return translated


class Qwen3Translator:
    """Local Qwen3 served by Ollama; translates a batch of cues with context."""

    supports_markers = False

    def __init__(self, *, source_language: str, target_language: str, timeout: float = 180.0, opener=None):
        self.source_language, self.target_language = validate_language_pair(source_language, target_language)
        if self.source_language == "auto":
            raise TranslationServiceError("translator requires a detected source language")
        self.model = local_model("qwen3")
        self.url = ollama_url()
        self.timeout = timeout
        self._open = opener or _direct_open
        self.name = f"qwen3-{self.model}"

    def translate_lines(self, lines: list[str]) -> list[str]:
        source_label = language_profile(self.source_language).label_vi
        target_label = language_profile(self.target_language).label_vi
        prompt = (
            f"Bạn là biên dịch viên phụ đề video. Dịch mảng câu từ {source_label} sang {target_label}. "
            "Dựa vào các câu liền nhau để hiểu ngữ cảnh, đại từ và cách xưng hô, giữ xưng hô nhất quán. "
            "Dịch sát nội dung nhưng diễn đạt tự nhiên, ngắn gọn để đọc thành tiếng; không giải thích, không thêm ý. "
            f'Trả về JSON dạng {{"translations": [...]}} có đúng {len(lines)} chuỗi theo đúng thứ tự.\n'
            + json.dumps(lines, ensure_ascii=False)
        )
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            # Qwen3 "thinking" adds minutes of hidden reasoning for no gain here.
            "think": False,
            "format": {
                "type": "object",
                "properties": {"translations": {"type": "array", "items": {"type": "string"}}},
                "required": ["translations"],
            },
            "options": {"temperature": 0.1, "num_ctx": 8192},
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with self._open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise TranslationConfigurationError(
                    f"Ollama chưa có model {self.model}; hãy chạy: ollama pull {self.model}"
                ) from exc
            raise TranslationServiceError(f"Qwen3 lỗi HTTP {exc.code}") from exc
        except (urllib.error.URLError, ConnectionError) as exc:
            raise TranslationConfigurationError("Không kết nối được Ollama cho Qwen3") from exc
        except Exception as exc:
            raise TranslationServiceError(f"Qwen3 lỗi: {type(exc).__name__}") from exc
        content = str((payload.get("message") or {}).get("content") or "")
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        try:
            values = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE))
            translated = values["translations"] if isinstance(values, dict) else values
        except (ValueError, KeyError, TypeError) as exc:
            raise TranslationServiceError("qwen3_invalid_translation_json") from exc
        if not isinstance(translated, list) or not all(isinstance(item, str) and item.strip() for item in translated):
            raise TranslationServiceError("qwen3_invalid_translation_array")
        if len(translated) != len(lines):
            raise TranslationServiceError("qwen3_translation_count_mismatch")
        return [item.strip() for item in translated]

    def translate(self, text: str) -> str:
        return self.translate_lines([text])[0]


class FallbackTranslator:
    """Primary translator with TranslateGemma as the secondary for failed calls.

    Once the primary reports a rate limit or a permanent error it is skipped
    for the rest of the job, so a blocked cloud API does not cost one failed
    request per cue.
    """

    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary
        self.supports_markers = getattr(primary, "supports_markers", True)
        self.primary_down = False
        self.secondary_used = False
        if hasattr(primary, "translate_lines"):
            self.translate_lines = self._translate_lines

    @property
    def name(self) -> str:
        primary = getattr(self.primary, "name", type(self.primary).__name__)
        return f"{primary}+{self.secondary.name}" if self.secondary_used else primary

    def _primary_failed(self, exc: Exception) -> None:
        if getattr(exc, "rate_limited", False) or getattr(exc, "permanent", False):
            self.primary_down = True

    def translate(self, text: str) -> str:
        if not self.primary_down:
            try:
                return self.primary.translate(text)
            except Exception as exc:
                self._primary_failed(exc)
                primary_error = exc
        else:
            primary_error = None
        if '<vts id="' in text:
            # Marker batches are a primary-only optimisation; let cues retry singly.
            raise primary_error or TranslationServiceError("secondary_translator_skips_batches")
        value = self.secondary.translate(text)
        self.secondary_used = True
        return value

    def _translate_lines(self, lines: list[str]) -> list[str]:
        if not self.primary_down:
            try:
                return self.primary.translate_lines(lines)
            except Exception as exc:
                self._primary_failed(exc)
        if hasattr(self.secondary, "translate_lines"):
            values = self.secondary.translate_lines(lines)
        else:
            values = [self.secondary.translate(line) for line in lines]
        self.secondary_used = True
        return values


def create_translation_provider(provider: str, *, source_language: str, target_language: str):
    resolved = normalize_translation_provider(provider)
    if resolved in LOCAL_TRANSLATORS:
        return LOCAL_TRANSLATORS[resolved](source_language=source_language, target_language=target_language)
    if resolved == "gemini":
        primary = GeminiContextTranslator(source_language=source_language, target_language=target_language)
    else:
        primary = GoogleVietnameseTranslator(source_language=source_language, target_language=target_language)
    backup = next((key for key in LOCAL_BACKUP_ORDER if local_model_available(key)), None)
    if backup:
        return FallbackTranslator(
            primary, LOCAL_TRANSLATORS[backup](source_language=source_language, target_language=target_language)
        )
    return primary


LOCAL_TRANSLATORS = {"translategemma": TranslateGemmaTranslator, "qwen3": Qwen3Translator}
