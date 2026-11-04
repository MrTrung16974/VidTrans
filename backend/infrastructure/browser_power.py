"""Park the interactive browser sidecars while nobody is looking at them.

The TikTok and source-login sidecars run a full Chromium whose open page
(autoplaying feeds, studio dashboards) keeps burning CPU that the media pipeline
needs. Parking navigates every page to about:blank: Chromium, its profile and
cookies stay alive, so cookie export and publishing automation keep working (they
open their own pages), while an idle about:blank costs next to nothing.

A browser wakes when someone opens the page that shows it (the client sends a
heartbeat), and parks again after ``idle_seconds`` without one. The owner can
switch a browser off: it then stays parked and heartbeats no longer wake it.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)

BLANK_URL = "about:blank"


def is_blank(url: str) -> bool:
    return not url or url.startswith(BLANK_URL) or url.startswith("chrome://newtab")


class PageDriver(Protocol):
    def list_urls(self) -> list[str]: ...
    def park(self) -> list[str]: ...
    def restore(self, urls: list[str]) -> None: ...


class CdpPageDriver:
    """Talks to a sidecar's Chromium over its private DevTools endpoint."""

    def __init__(self, cdp_url: str) -> None:
        self.cdp_url = cdp_url.rstrip("/")

    def list_urls(self) -> list[str]:
        # Plain HTTP: cheap enough for the idle sweep, unlike a Playwright session.
        with urllib.request.urlopen(f"{self.cdp_url}/json/list", timeout=3) as response:
            targets = json.load(response)
        return [str(target.get("url") or "") for target in targets if target.get("type") == "page"]

    def _with_pages(self, operation: Callable[[Any], Any]) -> Any:
        from playwright.sync_api import sync_playwright

        with urllib.request.urlopen(f"{self.cdp_url}/json/version", timeout=3) as response:
            ws_url = json.load(response)["webSocketDebuggerUrl"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(ws_url, timeout=8_000)
            if not browser.contexts:
                raise RuntimeError("Trình duyệt chưa sẵn sàng")
            # Disconnect only on exit; the sidecar's Chromium keeps running.
            return operation(browser.contexts[0])

    def park(self) -> list[str]:
        def park_pages(context: Any) -> list[str]:
            parked = []
            for page in context.pages:
                if is_blank(page.url):
                    continue
                parked.append(page.url)
                page.goto(BLANK_URL, timeout=10_000)
            return parked

        return self._with_pages(park_pages)

    def restore(self, urls: list[str]) -> None:
        def restore_pages(context: Any) -> None:
            blank = [page for page in context.pages if is_blank(page.url)]
            for index, url in enumerate(urls):
                page = blank[index] if index < len(blank) else context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        self._with_pages(restore_pages)


class BrowserPower:
    def __init__(
        self,
        name: str,
        start_url: str,
        settings_path: Path,
        driver: PageDriver,
        *,
        idle_seconds: float = 600.0,
        is_busy: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.start_url = start_url
        self.idle_seconds = idle_seconds
        self._settings_path = settings_path
        self._driver = driver
        self._is_busy = is_busy
        self._clock = clock
        self._lock = threading.Lock()
        self._parked_urls: list[str] = []
        # Counting from start-up: a browser nobody opens is parked after one idle period.
        self._last_seen = clock()
        self._enabled = self._load_enabled()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _load_enabled(self) -> bool:
        try:
            return bool(json.loads(self._settings_path.read_text(encoding="utf-8")).get("enabled", True))
        except (OSError, ValueError, AttributeError):
            return True

    def _save_enabled(self, enabled: bool) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
        temporary.replace(self._settings_path)

    def _awake(self) -> bool | None:
        """True while any page shows a site; None when the sidecar is unreachable."""
        try:
            return any(not is_blank(url) for url in self._driver.list_urls())
        except Exception:
            return None

    def status(self) -> dict[str, Any]:
        awake = self._awake()
        return {
            "browser": self.name,
            "enabled": self._enabled,
            "available": awake is not None,
            "awake": bool(awake),
            "idle_timeout_seconds": int(self.idle_seconds),
        }

    def _wake_locked(self) -> None:
        if self._awake() is False:
            urls = self._parked_urls or [self.start_url]
            logger.info("Waking %s browser: %s", self.name, urls)
            self._driver.restore(urls)
            self._parked_urls = []

    def _park_locked(self) -> None:
        if self._awake():
            parked = self._driver.park()
            if parked:
                self._parked_urls = parked
            logger.info("Parked idle %s browser (was on %s)", self.name, parked)

    def heartbeat(self) -> dict[str, Any]:
        """Someone is looking at this browser: keep it awake, waking it if needed."""
        with self._lock:
            if self._enabled:
                self._last_seen = self._clock()
                try:
                    self._wake_locked()
                except Exception:
                    logger.warning("Cannot wake %s browser", self.name, exc_info=True)
        return self.status()

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            self._save_enabled(enabled)
            self._enabled = enabled
            self._last_seen = self._clock()
            try:
                if enabled:
                    self._wake_locked()
                else:
                    self._park_locked()
            except Exception:
                logger.warning("Cannot switch %s browser %s", self.name, "on" if enabled else "off", exc_info=True)
        return self.status()

    def sweep(self) -> bool:
        """Park the browser when idle. Returns True when it was parked now."""
        with self._lock:
            idle = self._clock() - self._last_seen >= self.idle_seconds
            if self._enabled and not idle:
                return False
            try:
                if self._is_busy() or not self._awake():
                    return False
                self._park_locked()
                return True
            except Exception:
                logger.warning("Cannot park %s browser", self.name, exc_info=True)
                return False


class BrowserPowerSweeper:
    """One daemon thread that parks idle browsers every ``interval`` seconds."""

    def __init__(self, browsers: list[BrowserPower], *, interval: float = 60.0) -> None:
        self._browsers = browsers
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None or not self._browsers:
            return
        self._thread = threading.Thread(target=self._run, name="vidtrans-browser-power", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            for browser in self._browsers:
                browser.sweep()
