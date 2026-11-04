"""Run page scripts in the shared source-login Chromium over its DevTools endpoint."""

from __future__ import annotations

import json
import threading
import urllib.request
from typing import Any, Callable

from infrastructure.novel_sources.base import NovelSourceError


class CdpPageRunner:
    """Open a private page in the sidecar's Chromium, run ``operation`` on it, close it.

    The person using the Nguồn tab keeps their own page; one import at a time uses
    the browser, so a long chapter download never fights another for it.
    """

    def __init__(self, cdp_url: str) -> None:
        self.cdp_url = cdp_url.rstrip("/")
        self._lock = threading.Lock()

    def run(self, operation: Callable[[Any], Any]) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - installed in the backend image
            raise NovelSourceError("Playwright chưa được cài trong backend") from exc
        with self._lock:
            try:
                with urllib.request.urlopen(f"{self.cdp_url}/json/version", timeout=4) as response:
                    ws_url = str(json.load(response)["webSocketDebuggerUrl"])
            except (OSError, ValueError, KeyError) as exc:
                raise NovelSourceError("Không kết nối được trình duyệt nguồn (social-browser)") from exc
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(ws_url, timeout=8_000)
                if not browser.contexts:
                    raise NovelSourceError("Trình duyệt nguồn chưa sẵn sàng")
                page = browser.contexts[0].new_page()
                try:
                    return operation(page)
                finally:
                    page.close()
