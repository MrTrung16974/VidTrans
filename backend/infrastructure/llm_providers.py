"""JSON-producing LLM clients for story analysis (Gemini API or a local Ollama model)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

from infrastructure.translation_providers import _direct_open, local_model, local_model_available, ollama_url

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class LLMError(RuntimeError):
    rate_limited = False
    unavailable = False


class LLMRateLimitedError(LLMError):
    rate_limited = True


class LLMUnavailableError(LLMError):
    unavailable = True


class StoryLLM(Protocol):
    name: str

    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> str: ...


def gemini_models() -> list[str]:
    preferred = os.environ.get("VIDTRANS_COMIC_LLM_MODEL", "").strip()
    configured = [m.strip() for m in os.environ.get("GEMINI_TRANSLATION_MODELS", "").split(",") if m.strip()]
    defaults = [os.environ.get("GEMINI_TRANSLATION_MODEL", "").strip(), "gemini-2.5-flash", "gemini-2.5-flash-lite"]
    return list(dict.fromkeys(m for m in [preferred, *configured, *defaults] if m))


class GeminiJSONClient:
    def __init__(self, *, timeout: float = 120.0, opener=None) -> None:
        self.api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not self.api_key:
            raise LLMError("Gemini chưa được cấu hình (GEMINI_API_KEY)")
        self.models = gemini_models()
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen
        self.name = f"gemini-{self.models[0]}"

    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
        }).encode("utf-8")
        limited = False
        for model in self.models:
            request = urllib.request.Request(
                f"{GEMINI_BASE_URL}/{model}:generateContent",
                data=body,
                headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
                method="POST",
            )
            try:
                with self._open(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in {404, 429}:
                    limited = limited or exc.code == 429
                    continue  # Try the next configured model.
                if exc.code >= 500:
                    raise LLMUnavailableError(f"Gemini API lỗi HTTP {exc.code}") from exc
                raise LLMError(f"Gemini API lỗi HTTP {exc.code}") from exc
            except TimeoutError as exc:
                raise LLMUnavailableError(f"Gemini API không phản hồi sau {self.timeout:.0f} giây") from exc
            except (urllib.error.URLError, ConnectionError) as exc:
                raise LLMUnavailableError("Không kết nối được Gemini API") from exc
            self.name = f"gemini-{model}"
            try:
                parts = payload["candidates"][0]["content"]["parts"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMError("Gemini không trả về nội dung (có thể bị chặn bởi bộ lọc an toàn)") from exc
            return "".join(str(part.get("text") or "") for part in parts)
        if limited:
            raise LLMRateLimitedError("Các model Gemini đã hết hạn mức tạm thời (HTTP 429)")
        raise LLMError("Không tìm thấy model Gemini khả dụng: " + ", ".join(self.models))


class OllamaJSONClient:
    def __init__(self, *, timeout: float = 900.0, opener=None) -> None:
        self.model = local_model("qwen3")
        self.url = ollama_url()
        self.timeout = timeout
        self._open = opener or _direct_open
        self.name = f"qwen3-{self.model}"

    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "think": False,
            "format": schema,
            "options": {"temperature": 0.4, "num_ctx": 16384},
        }

        def chat(payload: dict[str, Any]) -> dict[str, Any]:
            request = urllib.request.Request(
                f"{self.url}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self._open(request, timeout=self.timeout) as response:
                return json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))

        try:
            try:
                payload = chat(body)
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                body.pop("think")  # Instruct-only tags reject the flag.
                payload = chat(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise LLMError(f"Ollama chưa có model {self.model}; hãy chạy: ollama pull {self.model}") from exc
            raise LLMUnavailableError(f"Ollama lỗi HTTP {exc.code}") from exc
        except TimeoutError as exc:
            raise LLMUnavailableError(f"Qwen3 không phản hồi sau {self.timeout:.0f} giây") from exc
        except (urllib.error.URLError, ConnectionError) as exc:
            raise LLMUnavailableError("Không kết nối được Ollama") from exc
        return str((payload.get("message") or {}).get("content") or "")


def llm_provider_statuses() -> list[dict[str, Any]]:
    return [
        {"key": "auto", "label": "Tự động (Gemini → Qwen3 → chia theo câu)", "configured": True},
        {"key": "gemini", "label": "Gemini AI", "configured": bool(os.environ.get("GEMINI_API_KEY", "").strip())},
        {"key": "qwen3", "label": "Qwen3 local (Ollama)", "configured": local_model_available("qwen3")},
        {"key": "heuristic", "label": "Chia theo câu (không dùng AI)", "configured": True},
    ]


def create_story_llm(provider: str) -> StoryLLM | None:
    """None means the rule-based splitter; 'auto' picks the best configured model."""
    if provider == "heuristic":
        return None
    if provider == "gemini" or (provider == "auto" and os.environ.get("GEMINI_API_KEY", "").strip()):
        return GeminiJSONClient()
    if provider == "qwen3":
        if not local_model_available("qwen3"):
            raise LLMError(f"Qwen3 chưa sẵn sàng. Hãy chạy Ollama và tải model: ollama pull {local_model('qwen3')}")
        return OllamaJSONClient()
    if provider == "auto" and local_model_available("qwen3"):
        return OllamaJSONClient()
    return None
