"""Bounded Google translation requests without shared mutable request state."""
from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlencode


class GoogleVietnameseTranslator:
    # Web translators do not promise to preserve custom HTML cue markers.
    supports_markers = False
    name = "google-vi"

    def __init__(self, *, timeout: float = 15, opener=None):
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def _get(self, base: str, params: dict) -> str:
        request = urllib.request.Request(
            base + "?" + urlencode(params),
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "vi,en;q=0.8"},
        )
        with self._open(request, timeout=self.timeout) as response:
            return response.read(2 * 1024 * 1024).decode("utf-8")

    def translate(self, text: str) -> str:
        errors = []
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
            errors.append(type(exc).__name__)

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
            errors.append(type(exc).__name__)
        # Do not expose query text, provider response HTML or connection secrets.
        raise RuntimeError("Google dịch không trả bản tiếng Việt hợp lệ (" + ", ".join(errors) + ")")

    @staticmethod
    def _valid(source: str, translated: str) -> bool:
        from pipeline.translation import contains_han

        return bool(translated.strip()) and translated.strip() != source.strip() and not contains_han(translated)
