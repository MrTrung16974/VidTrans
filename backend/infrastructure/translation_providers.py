from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from domain.languages import language_profile, validate_language_pair
from infrastructure.translation_prompt import (
    TranslationHistory,
    TranslationOutputError,
    is_traditional_chinese,
    leaks_source_script,
    parse_translations,
    system_prompt,
    user_prompt,
)
from infrastructure.vietnamese_translator import (
    GoogleVietnameseTranslator,
    TranslationConfigurationError,
    TranslationRateLimitedError,
    TranslationServiceError,
    TranslationUnavailableError,
)

logger = logging.getLogger(__name__)


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
    "vi": "Vietnamese", "en": "English", "zh-CN": "Chinese", "zh-TW": "Chinese", "ja": "Japanese",
    "ko": "Korean", "th": "Thai", "id": "Indonesian", "es": "Spanish",
}


def ollama_keep_alive() -> str:
    """Keep one job fast, then return the model's multi-GB allocation promptly."""

    return os.environ.get("VIDTRANS_OLLAMA_KEEP_ALIVE", "30s").strip() or "30s"


def ollama_context_size(name: str, default: int) -> int:
    try:
        return max(512, int(os.environ.get(name, str(default))))
    except ValueError:
        logger.warning("%s is invalid; using %d", name, default)
        return default


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


def installed_local_model(provider: str, *, opener=None) -> str:
    """The exact Ollama tag to request for this provider.

    A configured name without a tag ("translategemma") matches any installed tag in
    ``local_model_available``, but Ollama resolves an untagged request to ``:latest``
    and answers 404 when only e.g. ``translategemma:4b`` is installed. Requests must
    therefore use the installed tag.
    """
    wanted = local_model(provider)
    if ":" in wanted:
        return wanted
    tags = sorted(name for name in _installed_ollama_models(opener=opener) if name.split(":")[0] == wanted)
    if f"{wanted}:latest" in tags or not tags:
        return wanted
    return tags[0]


def default_translation_provider() -> str:
    """TranslateGemma when Ollama has it, otherwise the always-available Google Translate."""

    return "translategemma" if local_model_available("translategemma") else "google"


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


def translate_halving(translate_chunk, lines: list[str], durations: list[float | None] | None,
                      *, provider: str) -> list[str]:
    """Retry a batch the model mangled (bad JSON, wrong count, shifted lines) in halves.

    A long batch is where an LLM loses track of which line it is on; halves keep
    the context benefit, and a single line that still fails is a real failure.
    """
    try:
        return translate_chunk(lines, durations)
    except TranslationOutputError as exc:
        if len(lines) == 1:
            raise
        middle = len(lines) // 2
        logger.warning("%s returned unusable output for %d lines (%s); retrying in halves",
                       provider, len(lines), exc)
        return [
            *translate_halving(translate_chunk, lines[:middle], durations[:middle] if durations else None,
                               provider=provider),
            *translate_halving(translate_chunk, lines[middle:], durations[middle:] if durations else None,
                               provider=provider),
        ]


class GeminiContextTranslator:
    supports_markers = False
    accepts_durations = True

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
        self.history = TranslationHistory()

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

    def translate_lines(self, lines: list[str], durations: list[float | None] | None = None) -> list[str]:
        return translate_halving(self._translate_chunk, lines, durations, provider="Gemini")

    def _translate_chunk(self, lines: list[str], durations: list[float | None] | None = None) -> list[str]:
        body = json.dumps({
            "systemInstruction": {
                "parts": [{"text": system_prompt(self.source_language, self.target_language, "".join(lines))}]
            },
            "contents": [{"role": "user", "parts": [{"text": user_prompt(lines, durations, self.history.items())}]}],
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
            translated = parse_translations(text, len(lines), provider="gemini", sources=lines)
            self.history.extend(lines, translated)
            return translated
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise TranslationRateLimitedError("Gemini đã hết hạn mức tạm thời (HTTP 429)") from exc
            if exc.code == 404:
                raise TranslationConfigurationError(f"Gemini không tìm thấy model {self.model}") from exc
            if exc.code >= 500:
                raise TranslationUnavailableError(f"Gemini API lỗi HTTP {exc.code}") from exc
            raise TranslationServiceError(f"Gemini API lỗi HTTP {exc.code}") from exc
        except TranslationServiceError:
            raise
        except TimeoutError as exc:
            raise TranslationUnavailableError(
                f"Gemini API không phản hồi sau {self.timeout:.0f} giây"
            ) from exc
        except (urllib.error.URLError, ConnectionError) as exc:
            raise TranslationUnavailableError(
                f"Không kết nối được Gemini API ({type(getattr(exc, 'reason', exc)).__name__})"
            ) from exc
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
        self.model = installed_local_model("translategemma", opener=opener)
        self.url = ollama_url()
        self.timeout = timeout
        self._open = opener or _direct_open
        self.name = f"translategemma-{self.model}"

    def _prompt(self, text: str) -> str:
        source_code = language_profile(self.source_language).code
        if source_code == "zh-CN" and is_traditional_chinese(text):
            source_code = "zh-TW"
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
            # One subtitle cue is small; a large model-default context only reserves
            # extra KV memory and increases swap pressure on 8 GB hosts.
            "options": {
                "temperature": 0,
                "num_ctx": ollama_context_size("VIDTRANS_TRANSLATEGEMMA_NUM_CTX", 2048),
            },
            "keep_alive": ollama_keep_alive(),
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
            if exc.code >= 500:
                raise TranslationUnavailableError(f"TranslateGemma lỗi HTTP {exc.code}") from exc
            raise TranslationServiceError(f"TranslateGemma lỗi HTTP {exc.code}") from exc
        except (urllib.error.URLError, ConnectionError) as exc:
            raise TranslationConfigurationError("Không kết nối được Ollama cho TranslateGemma") from exc
        except TimeoutError as exc:
            raise TranslationUnavailableError(
                f"TranslateGemma không phản hồi sau {self.timeout:.0f} giây"
            ) from exc
        except Exception as exc:
            raise TranslationServiceError(f"TranslateGemma lỗi: {type(exc).__name__}") from exc
        translated = str((payload.get("message") or {}).get("content") or "").strip()
        if not translated:
            raise TranslationServiceError("translategemma_empty_translation")
        return translated


class Qwen3Translator:
    """Local Qwen3 served by Ollama; translates a batch of cues with context."""

    supports_markers = False
    accepts_durations = True

    def __init__(self, *, source_language: str, target_language: str, timeout: float = 180.0, opener=None,
                 rescue=None):
        self.source_language, self.target_language = validate_language_pair(source_language, target_language)
        if self.source_language == "auto":
            raise TranslationServiceError("translator requires a detected source language")
        # Translates the rare cue the model keeps copying in the source script.
        self._rescue = rescue
        self.model = installed_local_model("qwen3", opener=opener)
        self.url = ollama_url()
        self.timeout = timeout
        self._open = opener or _direct_open
        self.name = f"qwen3-{self.model}"
        self.history = TranslationHistory()
        # A CPU-bound local model cannot finish a long batch within the timeout; send small chunks
        # and shrink them further whenever one still times out.
        try:
            self.chunk_lines = max(1, int(os.environ.get("VIDTRANS_QWEN3_BATCH_LINES", "").strip() or 12))
        except ValueError:
            self.chunk_lines = 12

    def _chat(self, lines: list[str], durations: list[float | None] | None) -> list[str]:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt(self.source_language, self.target_language, "".join(lines))},
                {"role": "user", "content": user_prompt(lines, durations, self.history.items())},
            ],
            "stream": False,
            # Qwen3 "thinking" adds minutes of hidden reasoning for no gain here.
            "think": False,
            "format": {
                "type": "object",
                "properties": {
                    "translations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"}, "source": {"type": "string"}, "text": {"type": "string"},
                            },
                            "required": ["id", "source", "text"],
                        },
                    }
                },
                "required": ["translations"],
            },
            "options": {
                "temperature": 0.1,
                "num_ctx": ollama_context_size("VIDTRANS_QWEN3_NUM_CTX", 8192),
            },
            "keep_alive": ollama_keep_alive(),
        }

        def chat(payload: dict) -> dict:
            request = urllib.request.Request(
                f"{self.url}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self._open(request, timeout=self.timeout) as response:
                return json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))

        try:
            try:
                payload = chat(body)
            except urllib.error.HTTPError as exc:
                # Instruct-only tags (e.g. qwen3:4b-instruct) reject the think flag.
                if exc.code != 400:
                    raise
                body.pop("think")
                payload = chat(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise TranslationConfigurationError(
                    f"Ollama chưa có model {self.model}; hãy chạy: ollama pull {self.model}"
                ) from exc
            if exc.code >= 500:
                raise TranslationUnavailableError(f"Qwen3 lỗi HTTP {exc.code}") from exc
            raise TranslationServiceError(f"Qwen3 lỗi HTTP {exc.code}") from exc
        except (urllib.error.URLError, ConnectionError) as exc:
            raise TranslationConfigurationError("Không kết nối được Ollama cho Qwen3") from exc
        except TimeoutError as exc:
            message = f"Qwen3 không phản hồi sau {self.timeout:.0f} giây"
            if len(lines) > 1:
                # A slow CPU may miss the deadline on a long batch yet still translate single cues.
                error = TranslationServiceError(f"{message} cho {len(lines)} câu")
                error.timed_out = True
                raise error from exc
            raise TranslationUnavailableError(message) from exc
        except Exception as exc:
            raise TranslationServiceError(f"Qwen3 lỗi: {type(exc).__name__}") from exc
        content = str((payload.get("message") or {}).get("content") or "")
        return parse_translations(content, len(lines), provider="qwen3", sources=lines)

    def translate_lines(self, lines: list[str], durations: list[float | None] | None = None) -> list[str]:
        translated: list[str] = []
        start = 0
        while start < len(lines):
            end = start + self.chunk_lines
            chunk_durations = durations[start:end] if durations else None
            try:
                translated.extend(translate_halving(
                    self._translate_chunk, lines[start:end], chunk_durations, provider="Qwen3"
                ))
            except TranslationServiceError as exc:
                if not getattr(exc, "timed_out", False):
                    raise
                self.chunk_lines = max(1, min(self.chunk_lines, len(lines) - start) // 2)
                logger.warning("Qwen3 timed out; translating %d cues per request", self.chunk_lines)
                continue
            start = end
        return translated

    def _translate_chunk(self, lines: list[str], durations: list[float | None] | None) -> list[str]:
        translated = self._chat(lines, durations)
        # A small model occasionally copies a phrase untranslated; retry those cues alone.
        for index, value in enumerate(translated):
            if leaks_source_script(value, self.target_language):
                single = [durations[index]] if durations else None
                retried = self._chat([lines[index]], single)[0]
                if leaks_source_script(retried, self.target_language):
                    retried = self._rescue_line(lines[index])
                translated[index] = retried
        self.history.extend(lines, translated)
        return translated

    def _rescue_line(self, line: str) -> str:
        if self._rescue is None:
            self._rescue = GoogleVietnameseTranslator(
                source_language=self.source_language, target_language=self.target_language
            )
        try:
            value = self._rescue.translate(line)
        except Exception as exc:
            raise TranslationServiceError("qwen3_untranslated_source_text") from exc
        if not value or leaks_source_script(value, self.target_language):
            raise TranslationServiceError("qwen3_untranslated_source_text")
        logger.info("Qwen3 kept the source script; used %s for one cue", getattr(self._rescue, "name", "rescue"))
        return value

    def translate(self, text: str) -> str:
        return self.translate_lines([text])[0]


_BLOCKING_FLAGS = ("rate_limited", "permanent", "unavailable")


def _blocked(error: BaseException) -> bool:
    return any(getattr(error, flag, False) for flag in _BLOCKING_FLAGS)


def _combined_failure(primary_error: BaseException | None, secondary_error: BaseException) -> BaseException:
    """One error for a cue both translators failed.

    The message names both causes. It blocks the rest of the job (rate limit,
    configuration, outage) only when *both* translators are blocked: a broken backup
    must not stop a primary that merely failed on one cue.
    """
    if primary_error is None:  # The primary was already marked down for this job.
        return secondary_error
    combined = copy.copy(secondary_error)
    combined.args = (f"{primary_error}; dự phòng: {secondary_error}"[:400],)
    for flag in _BLOCKING_FLAGS:
        setattr(combined, flag, bool(getattr(primary_error, flag, False) and getattr(secondary_error, flag, False)))
    if _blocked(primary_error) and _blocked(secondary_error) and not _blocked(combined):
        combined.permanent = True  # Blocked for different reasons: still nothing left to try.
    return combined


class FallbackTranslator:
    """Primary translator with a secondary (possibly another FallbackTranslator) for failed calls.

    Once the primary reports a rate limit, an outage or a permanent error it is
    skipped for the rest of the job, so a blocked provider does not cost one
    failed request per cue.
    """

    accepts_durations = True

    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary
        self.supports_markers = getattr(primary, "supports_markers", True)
        self.primary_down = False
        self.secondary_used = False
        # Source lines the secondary translated, so each cue can report who translated it.
        self.fallback_sources: set[str] = set()
        if hasattr(primary, "translate_lines"):
            self.translate_lines = self._translate_lines

    @property
    def name(self) -> str:
        primary = getattr(self.primary, "name", type(self.primary).__name__)
        return f"{primary}+{self.secondary.name}" if self.secondary_used else primary

    def used_fallback_for(self, source: str) -> bool:
        return source in self.fallback_sources

    def provider_for(self, source: str) -> str:
        if source in self.fallback_sources:
            secondary_provider = getattr(self.secondary, "provider_for", None)
            return secondary_provider(source) if secondary_provider else getattr(self.secondary, "name", "secondary")
        return getattr(self.primary, "name", type(self.primary).__name__)

    def _primary_failed(self, exc: Exception) -> None:
        if any(getattr(exc, flag, False) for flag in _BLOCKING_FLAGS):
            self.primary_down = True
            logger.warning("Translator %s blocked (%s); using %s for the rest of the job",
                           getattr(self.primary, "name", "primary"), type(exc).__name__,
                           getattr(self.secondary, "name", "secondary"))

    def translate(self, text: str) -> str:
        primary_error = None
        if not self.primary_down:
            try:
                return self.primary.translate(text)
            except Exception as exc:
                self._primary_failed(exc)
                primary_error = exc
        if '<vts id="' in text:
            # Marker batches are a primary-only optimisation; let cues retry singly.
            raise primary_error or TranslationServiceError("secondary_translator_skips_batches")
        try:
            value = self.secondary.translate(text)
        except Exception as exc:
            raise _combined_failure(primary_error, exc) from exc
        self.secondary_used = True
        self.fallback_sources.add(text)
        return value

    @staticmethod
    def _call_lines(translator, lines: list[str], durations: list[float | None] | None) -> list[str]:
        if getattr(translator, "accepts_durations", False):
            return translator.translate_lines(lines, durations=durations)
        return translator.translate_lines(lines)

    def _translate_lines(self, lines: list[str], durations: list[float | None] | None = None) -> list[str]:
        primary_error = None
        if not self.primary_down:
            try:
                return self._call_lines(self.primary, lines, durations)
            except Exception as exc:
                self._primary_failed(exc)
                primary_error = exc
        try:
            if hasattr(self.secondary, "translate_lines"):
                values = self._call_lines(self.secondary, lines, durations)
            else:
                values = [self.secondary.translate(line) for line in lines]
        except Exception as exc:
            raise _combined_failure(primary_error, exc) from exc
        self.secondary_used = True
        self.fallback_sources.update(lines)
        logger.warning("%s could not translate %d lines (%s); used %s",
                       getattr(self.primary, "name", "primary"), len(lines),
                       type(primary_error).__name__ if primary_error else "provider down",
                       getattr(self.secondary, "name", "secondary"))
        return values


def create_translation_provider(provider: str, *, source_language: str, target_language: str):
    """The chosen translator, backed by every other working one.

    Order: chosen -> Google -> ready local models. One broken provider (missing Ollama
    model, exhausted quota, outage) then costs a fallback, not the whole job.
    """
    resolved = normalize_translation_provider(provider)
    languages = {"source_language": source_language, "target_language": target_language}
    if resolved in LOCAL_TRANSLATORS:
        chain = [LOCAL_TRANSLATORS[resolved](**languages)]
    elif resolved == "gemini":
        chain = [GeminiContextTranslator(**languages)]
    else:
        chain = []
    if resolved != "google":
        chain.append(GoogleVietnameseTranslator(**languages))
    else:
        chain.insert(0, GoogleVietnameseTranslator(**languages))
    chain += [LOCAL_TRANSLATORS[key](**languages) for key in LOCAL_BACKUP_ORDER
              if key != resolved and local_model_available(key)]
    translator = chain[-1]
    for primary in reversed(chain[:-1]):
        translator = FallbackTranslator(primary, translator)
    return translator


LOCAL_TRANSLATORS = {"translategemma": TranslateGemmaTranslator, "qwen3": Qwen3Translator}
