"""Bounded multilingual translation requests without shared mutable state."""
from __future__ import annotations

import html
import json
import re
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from urllib.parse import urlencode

from domain.languages import language_profile, validate_language_pair


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


def _parse_single(payload) -> str:
    # Concatenate every sentence, not just the first response fragment.
    return "".join(part[0] for part in payload[0] if part and isinstance(part[0], str))


def _parse_dict_chrome(payload) -> str:
    # ["text"] with a fixed source language, [["text", "zh-CN"]] with auto-detect.
    first = payload[0] if isinstance(payload, list) and payload else ""
    if isinstance(first, list):
        first = first[0] if first else ""
    return first if isinstance(first, str) else ""


_GTX = {"client": "gtx", "dt": "t"}
# Separate Google hosts/clients are rate limited independently; on HTTP 429 the
# next one is used instead of failing the whole job.
_JSON_ENDPOINTS = (
    ("googleapis", "https://translate.googleapis.com/translate_a/single", _GTX, _parse_single),
    ("dict-chrome", "https://clients5.google.com/translate_a/t",
     {"client": "dict-chrome-ex"}, _parse_dict_chrome),
    ("google-com", "https://translate.google.com/translate_a/single", _GTX, _parse_single),
)
_RATE_LIMIT_COOLDOWN = 30.0
_MYMEMORY_ENDPOINT = "https://api.mymemory.translated.net/get"


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
    name = "google"

    def __init__(self, *, source_language: str = "zh-CN", target_language: str = "vi",
                 timeout: float = 15, opener=None, min_interval: float | None = None):
        import os
        self.source_language, self.target_language = validate_language_pair(source_language, target_language)
        if self.source_language == "auto":
            raise TranslationServiceError("translator requires a detected source language")
        self.source_profile = language_profile(self.source_language)
        self.target_profile = language_profile(self.target_language)
        self.name = f"google-{self.source_language}-{self.target_language}"
        self.timeout = timeout
        # Space out requests so a long video does not trip Google's rate limit.
        if min_interval is None:
            min_interval = 0.0 if opener else float(os.environ.get("VIDTRANS_TRANSLATION_MIN_INTERVAL", "0.4"))
        self.min_interval = max(0.0, min_interval)
        self._last_request = 0.0
        self._cooldown_until: dict[str, float] = {}
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

    def _cooling(self, name: str) -> bool:
        return self._cooldown_until.get(name, 0.0) > time.monotonic()

    def _mark_rate_limited(self, name: str, retry_after: float | None) -> None:
        self._cooldown_until[name] = time.monotonic() + max(retry_after or 0.0, _RATE_LIMIT_COOLDOWN)

    def _rate_limited(self, errors: list[str]) -> TranslationRateLimitedError:
        now = time.monotonic()
        # Callers back off until the first endpoint is usable again.
        wait = min((until - now for until in self._cooldown_until.values() if until > now), default=None)
        return TranslationRateLimitedError(
            "Google dịch giới hạn tần suất (" + ", ".join(errors or ["http_429"]) + ")", wait
        )

    def _query_json(self, text: str, errors: list[str], valid) -> str | None:
        """Try each JSON endpoint not cooling down after a 429; return the first valid result."""
        for name, base, params, parse in _JSON_ENDPOINTS:
            if self._cooling(name):
                errors.append("http_429")
                continue
            try:
                translated = parse(json.loads(self._get(base, {
                    **params,
                    "sl": self.source_profile.google_code,
                    "tl": self.target_profile.google_code,
                    "q": text,
                })))
            except Exception as exc:
                errors.append(failure_code(exc))
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                    self._mark_rate_limited(name, _retry_after(exc))
                continue
            if valid(translated):
                return translated
            errors.append("invalid_translation")
        return None

    def _query_mymemory(self, text: str) -> str:
        """Use an independent provider when Google rejects the server IP."""
        if self._cooling("mymemory"):
            wait = self._cooldown_until["mymemory"] - time.monotonic()
            raise TranslationRateLimitedError(
                "MyMemory giới hạn tần suất (http_429)", max(wait, 0.0)
            )
        if len(text.encode("utf-8")) > 500:
            raise TranslationServiceError("MyMemory: query_too_long")
        try:
            payload = json.loads(self._get(
                _MYMEMORY_ENDPOINT,
                {"q": text, "langpair": f"{self.source_profile.mymemory_code}|{self.target_profile.mymemory_code}", "mt": "1"},
            ))
            status = int(payload.get("responseStatus", 0))
            translated = html.unescape(
                str(payload.get("responseData", {}).get("translatedText", ""))
            ).strip()
            if status == 429:
                self._mark_rate_limited("mymemory", None)
                raise TranslationRateLimitedError("MyMemory giới hạn tần suất (http_429)")
            if status != 200 or not self._valid(text, translated):
                raise TranslationServiceError(f"MyMemory: invalid_response_{status}")
            return translated
        except TranslationServiceError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                self._mark_rate_limited("mymemory", _retry_after(exc))
                raise TranslationRateLimitedError(
                    "MyMemory giới hạn tần suất (http_429)", _retry_after(exc)
                ) from exc
            raise TranslationServiceError(f"MyMemory: http_{exc.code}") from exc
        except Exception as exc:
            raise TranslationServiceError(f"MyMemory: {failure_code(exc)}") from exc

    def translate_lines(self, lines: list[str]) -> list[str]:
        """Translate many single-line cues in one request; Google keeps line breaks."""
        if any("\n" in line for line in lines):
            raise ValueError("multiline_cue")
        errors: list[str] = []

        def valid(translated: str) -> bool:
            result = [line.strip() for line in translated.split("\n")]
            return len(result) == len(lines) and all(self._valid(src, out) for src, out in zip(lines, result))

        translated = self._query_json("\n".join(lines), errors, valid)
        if translated is not None:
            return [line.strip() for line in translated.split("\n")]
        try:
            # This provider limits a request to 500 UTF-8 bytes and may not
            # preserve newlines, so translate each complete cue separately.
            return [self._query_mymemory(line) for line in lines]
        except TranslationRateLimitedError:
            raise
        except TranslationServiceError as exc:
            errors.append(str(exc))
        if all(code == "http_429" for code in errors):
            raise self._rate_limited(errors)
        if "invalid_translation" in errors:
            raise ValueError("invalid_batch_translation")
        raise TranslationServiceError("Google dịch lỗi (" + ", ".join(errors) + ")")

    def translate(self, text: str) -> str:
        errors: list[str] = []
        translated = self._query_json(text, errors, lambda value: self._valid(text, value))
        if translated is not None:
            return translated

        if self._cooling("mobile"):
            errors.append("http_429")
        else:
            try:
                from bs4 import BeautifulSoup

                page = self._get("https://translate.google.com/m", {
                    "sl": self.source_profile.google_code,
                    "tl": self.target_profile.google_code,
                    "q": text,
                })
                soup = BeautifulSoup(page, "html.parser")
                node = soup.select_one(".result-container, .t0")
                translated = node.get_text(" ", strip=True) if node else ""
                if self._valid(text, translated):
                    return translated
                errors.append("invalid_translation")
            except Exception as exc:
                errors.append(failure_code(exc))
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                    self._mark_rate_limited("mobile", _retry_after(exc))
        try:
            return self._query_mymemory(text)
        except TranslationRateLimitedError:
            raise
        except TranslationServiceError as exc:
            errors.append(str(exc))
        # Do not expose query text, provider response HTML or connection secrets.
        if all(code == "http_429" for code in errors):
            raise self._rate_limited(errors)
        raise TranslationServiceError(
            f"Dịch {self.source_language}→{self.target_language} không trả kết quả hợp lệ ("
            + ", ".join(errors) + ")"
        )

    def _valid(self, source: str, translated: str) -> bool:
        source_value = source.strip()
        result = translated.strip()
        if not result or result.casefold() == source_value.casefold():
            return False
        contains_han = re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", result) is not None
        source_has_han = re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", source_value) is not None
        return not (source_has_han and contains_han and self.target_language not in {"zh-CN", "ja"})


MultilingualTranslator = GoogleVietnameseTranslator
