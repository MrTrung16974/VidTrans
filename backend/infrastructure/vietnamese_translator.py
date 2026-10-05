"""Bounded Google translation requests without shared mutable request state."""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from urllib.parse import urlencode


class TranslationServiceError(RuntimeError):
    """Sanitized provider failure safe to include in translation diagnostics."""


class TranslationRateLimitedError(TranslationServiceError):
    """Provider answered HTTP 429; callers should back off instead of hammering."""
    rate_limited = True

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(error) -> float | None:
    try:
        value = float(error.headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def failure_code(error):
    if isinstance(error, urllib.error.HTTPError):
        return f"http_{error.code}"
    if isinstance(error, (TimeoutError,)):
        return "timeout"
    if isinstance(error, urllib.error.URLError):
        return "connection_failed"
    return type(error).__name__


class GoogleVietnameseTranslator:
    # Web translators do not promise to preserve custom HTML cue markers.
    supports_markers = False
    name = "google-vi"

    def __init__(self, *, timeout: float = 15, opener=None, min_interval: float | None = None):
        import os
        self.timeout = timeout
        # Space out requests so a long video does not trip Google's rate limit.
        if min_interval is None:
            min_interval = 0.0 if opener else float(os.environ.get("VIDTRANS_TRANSLATION_MIN_INTERVAL", "0.4"))
        self.min_interval = max(0.0, min_interval)
        self._last_request = 0.0
        if opener:
            self._open = opener
        else:
            # Browser proxies may be SOCKS or require a browser-only auth flow.
            # Translation has its own explicit HTTP(S) proxy configuration.
            proxy_url = os.environ.get("VIDTRANS_TRANSLATION_PROXY", "").strip()
            if proxy_url:
                try:
                    parsed = urlsplit(proxy_url)
                    valid = parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port != 0
                except ValueError:
                    valid = False
                if not valid:
                    raise TranslationServiceError("translation_proxy_invalid: dùng proxy HTTP hoặc HTTPS")
            handler = urllib.request.ProxyHandler(
                {'http': proxy_url, 'https': proxy_url} if proxy_url else {}
            )
            self._open = urllib.request.build_opener(handler).open

    def _get(self, base: str, params: dict) -> str:
        request = urllib.request.Request(
            base + "?" + urlencode(params),
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "vi,en;q=0.8"},
        )
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        with self._open(request, timeout=self.timeout) as response:
            return response.read(2 * 1024 * 1024).decode("utf-8")

    def translate_lines(self, lines: list[str]) -> list[str]:
        """Translate many single-line cues in one request; Google keeps line breaks."""
        if any("\n" in line for line in lines):
            raise ValueError("multiline_cue")
        try:
            payload = json.loads(self._get("https://translate.googleapis.com/translate_a/single", {
                "client": "gtx", "sl": "zh-CN", "tl": "vi", "dt": "t", "q": "\n".join(lines),
            }))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise TranslationRateLimitedError("Google dịch giới hạn tần suất (http_429)", _retry_after(exc)) from None
            raise TranslationServiceError("Google dịch lỗi (" + failure_code(exc) + ")") from None
        except Exception as exc:
            raise TranslationServiceError("Google dịch lỗi (" + failure_code(exc) + ")") from None
        translated = "".join(part[0] for part in payload[0] if part and isinstance(part[0], str))
        result = [line.strip() for line in translated.split("\n")]
        if len(result) != len(lines) or not all(self._valid(src, out) for src, out in zip(lines, result)):
            raise ValueError("invalid_batch_translation")
        return result

    def translate(self, text: str) -> str:
        errors = []
        retry_after = None
        try:
            payload = json.loads(self._get("https://translate.googleapis.com/translate_a/single", {
                "client": "gtx", "sl": "zh-CN", "tl": "vi", "dt": "t", "q": text,
            }))
            # Concatenate every sentence, not just the first response fragment.
            translated = "".join(part[0] for part in payload[0] if part and isinstance(part[0], str))
            if self._valid(text, translated):
                return translated
            errors.append("invalid_translation")
        except Exception as exc:
            errors.append(failure_code(exc))
            retry_after = _retry_after(exc)

        try:
            from bs4 import BeautifulSoup

            page = self._get("https://translate.google.com/m", {"sl": "zh-CN", "tl": "vi", "q": text})
            soup = BeautifulSoup(page, "html.parser")
            node = soup.select_one(".result-container, .t0")
            translated = node.get_text(" ", strip=True) if node else ""
            if self._valid(text, translated):
                return translated
            errors.append("invalid_translation")
        except Exception as exc:
            errors.append(failure_code(exc))
        # Do not expose query text, provider response HTML or connection secrets.
        if all(code == "http_429" for code in errors):
            raise TranslationRateLimitedError(
                "Google dịch giới hạn tần suất (" + ", ".join(errors) + ")", retry_after
            )
        raise TranslationServiceError("Google dịch không trả bản tiếng Việt hợp lệ (" + ", ".join(errors) + ")")

    @staticmethod
    def _valid(source: str, translated: str) -> bool:
        from pipeline.translation import contains_han

        return bool(translated.strip()) and translated.strip() != source.strip() and not contains_han(translated)
