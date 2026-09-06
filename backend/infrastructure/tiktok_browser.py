"""Interactive TikTok preparation. Never clicks Publish or retries an upload.

The application runs one backend process and one dedicated Chromium profile.
Durable attempts reserve that profile until a human resolves the Studio page.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)
STUDIO_URL = "https://www.tiktok.com/tiktokstudio/upload"
BUSY_STATES = {"queued", "opening", "uploading"}


class TikTokBrowserError(RuntimeError):
    pass


def is_tiktok_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in {"www.tiktok.com", "tiktok.com"}


def has_session(cookies: list[dict[str, Any]]) -> bool:
    return any(
        str(c.get("domain", "")).lstrip(".") in {"tiktok.com", "www.tiktok.com"}
        and c.get("name") in {"sessionid", "sessionid_ss"}
        and bool(c.get("value"))
        and (float(c.get("expires", -1)) == -1 or float(c.get("expires", 0)) > time.time())
        for c in cookies
    )


class TikTokBrowserManager:
    def __init__(self, work_dir: Path, *, cdp_url: str | None = None, public_url: str | None = None):
        work_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = work_dir / "attempts.sqlite3"
        self.cdp_url = cdp_url or os.environ.get("VIDTRANS_TIKTOK_BROWSER_CDP_URL", "http://tiktok-browser:9222")
        self.public_url = public_url or os.environ.get(
            "VIDTRANS_TIKTOK_BROWSER_PUBLIC_URL",
            "http://localhost:5202/vnc_lite.html?autoconnect=true&resize=scale&reconnect=true&path=websockify",
        )
        self._lock = threading.RLock()
        self._browser_lock = threading.Lock()
        self._session_present = False
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, state TEXT NOT NULL, caption TEXT NOT NULL, message TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_browser ON attempts(active) WHERE active=1")
            db.execute("UPDATE attempts SET state='needs_review', message=? WHERE active=1 AND state IN ('queued','opening','uploading')", (
                "Máy chủ đã khởi động lại. Kiểm tra bài trong TikTok Studio trước khi tiếp tục; hệ thống không tự tải lại.",
            ))
        self.db_path.chmod(0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def active_attempt(self) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM attempts WHERE active=1").fetchone()
        return dict(row) if row else None

    def latest_attempt(self, job_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM attempts WHERE job_id=? ORDER BY created_at DESC LIMIT 1", (job_id,)).fetchone()
        return dict(row) if row else None

    def _update(self, attempt_id: str, state: str, message: str):
        with self._db() as db:
            db.execute("UPDATE attempts SET state=?, message=? WHERE id=? AND active=1", (state, message, attempt_id))

    def _with_browser(self, operation: Callable[[Any], Any]):
        from playwright.sync_api import sync_playwright
        with urllib.request.urlopen(f"{self.cdp_url.rstrip('/')}/json/version", timeout=3) as response:
            ws_url = json.load(response)["webSocketDebuggerUrl"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(ws_url, timeout=8_000)
            if not browser.contexts:
                raise TikTokBrowserError("Trình duyệt chưa sẵn sàng")
            return operation(browser)  # Disconnect only; preserve the user's Chromium.

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        available = False
        try:
            with urllib.request.urlopen(f"{self.cdp_url.rstrip('/')}/json/version", timeout=2) as response:
                available = response.status == 200
        except (OSError, ValueError):
            pass
        if refresh and available and self._browser_lock.acquire(blocking=False):
            try:
                self._session_present = self._with_browser(lambda b: has_session(b.contexts[0].cookies()))
            except Exception:
                self._session_present = False
            finally:
                self._browser_lock.release()
        if not available:
            self._session_present = False
        return {
            "available": available,
            "session_present": self._session_present,
            "browser_url": self.public_url if available else None,
            "attempt": self.active_attempt(),
            "message": "Trình duyệt chưa sẵn sàng" if not available else (
                "Đã tìm thấy phiên đăng nhập · Kiểm tra tài khoản trong TikTok Studio" if self._session_present
                else "Mở TikTok Studio để đăng nhập và kiểm tra tài khoản"
            ),
        }

    @staticmethod
    def _page(browser):
        context = browser.contexts[0]
        return next((p for p in context.pages if is_tiktok_url(p.url)), None) or context.new_page()

    def open_studio(self):
        if not self._browser_lock.acquire(blocking=False):
            raise TikTokBrowserError("Đang chuẩn bị video. Hãy chờ thao tác hoàn tất")
        try:
            def open_page(browser):
                page = self._page(browser)
                # Preserve an active draft, login challenge or manually edited page.
                if not is_tiktok_url(page.url):
                    page.goto(STUDIO_URL, wait_until="domcontentloaded", timeout=30_000)
                page.bring_to_front()
            self._with_browser(open_page)
        except Exception as exc:
            raise TikTokBrowserError("Không mở được trình duyệt TikTok. Kiểm tra dịch vụ tiktok-browser") from exc
        finally:
            self._browser_lock.release()
        return self.status(refresh=True)

    def logout(self):
        with self._lock:
            if self.active_attempt():
                raise TikTokBrowserError("Hãy kiểm tra và kết thúc bài đang chuẩn bị trước khi ngắt kết nối")
            if not self._browser_lock.acquire(blocking=False):
                raise TikTokBrowserError("Trình duyệt đang bận")
            try:
                def clear(browser):
                    context = browser.contexts[0]
                    context.clear_cookies()
                    for page in context.pages:
                        if is_tiktok_url(page.url):
                            cdp = context.new_cdp_session(page)
                            cdp.send("Storage.clearDataForOrigin", {"origin": "https://www.tiktok.com", "storageTypes": "all"})
                            cdp.detach()
                            page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded", timeout=30_000)
                    self._session_present = False
                self._with_browser(clear)
            except Exception as exc:
                raise TikTokBrowserError("Chưa xóa được phiên TikTok. Hãy thử lại khi trình duyệt sẵn sàng") from exc
            finally:
                self._browser_lock.release()
        return self.status()

    def prepare(self, job_id: str, video_path: Path, caption: str) -> dict[str, Any]:
        caption = caption.strip()
        if not caption or len(caption) > 2200:
            raise TikTokBrowserError("Nhập caption từ 1 đến 2.200 ký tự")
        if not video_path.is_file() or not video_path.stat().st_size:
            raise TikTokBrowserError("Không tìm thấy video đầu ra")
        with self._lock:
            active = self.active_attempt()
            if active:
                if active["job_id"] == job_id:
                    return active  # Network retries/double clicks never upload again.
                raise TikTokBrowserError("Có bài đang mở trong TikTok Studio. Kiểm tra và kết thúc bài đó trước")
            if not self._browser_lock.acquire(blocking=False):
                raise TikTokBrowserError("Trình duyệt đang bận. Hãy thử lại sau")
            attempt_id = uuid.uuid4().hex
            try:
                with self._db() as db:
                    db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,1,?)", (
                        attempt_id, job_id, "queued", caption, "Đang chờ mở video trong TikTok Studio", time.time(),
                    ))
                threading.Thread(target=self._run_prepare, args=(attempt_id, video_path.resolve(), caption), daemon=True).start()
            except Exception:
                self._browser_lock.release()
                self._update(attempt_id, "needs_review", "Không khởi chạy được tác vụ. Kiểm tra trình duyệt trước khi thử lại")
                raise
            return self.active_attempt() or {}

    def _run_prepare(self, attempt_id: str, video_path: Path, caption: str):
        try:
            self._update(attempt_id, "opening", "Đang mở trang tải video")
            self._with_browser(lambda browser: self._fill_upload(browser, attempt_id, video_path, caption))
        except Exception as exc:
            logger.warning("TikTok preparation stopped: %s", type(exc).__name__)
            self._update(attempt_id, "needs_review", "Chưa thể hoàn tất tự động. Mở TikTok Studio để kiểm tra đăng nhập, xác minh hoặc video đang tải. Không tự động thử lại.")
        finally:
            self._browser_lock.release()

    def _fill_upload(self, browser, attempt_id: str, video_path: Path, caption: str):
        page = self._page(browser)
        page.bring_to_front()
        # Dismiss is Playwright's default for beforeunload, preserving manual drafts.
        page.goto(STUDIO_URL, wait_until="domcontentloaded", timeout=30_000)
        if not has_session(browser.contexts[0].cookies()):
            self._session_present = False
            self._update(attempt_id, "needs_login", "Đăng nhập hoặc hoàn tất xác minh trong TikTok Studio, rồi kết thúc lượt này và chuẩn bị lại")
            return
        self._session_present = True
        inputs = page.locator('input[type="file"]')
        inputs.first.wait_for(state="attached", timeout=20_000)
        if not is_tiktok_url(page.url) or not urllib.parse.urlsplit(page.url).path.startswith("/tiktokstudio/upload") or inputs.count() != 1:
            raise TikTokBrowserError("Không nhận diện được ô tải video TikTok")
        self._update(attempt_id, "uploading", "Đang chuyển video sang TikTok. Hãy giữ nguyên trang trong lúc chuẩn bị")
        # Both containers mount outputs at the same absolute path; no public file URL.
        inputs.set_input_files(str(video_path), timeout=30_000)
        editor = page.locator('[contenteditable="true"][role="textbox"]:visible')
        editor.first.wait_for(state="visible", timeout=45_000)
        if not is_tiktok_url(page.url) or editor.count() != 1:
            raise TikTokBrowserError("Không nhận diện được ô caption duy nhất")
        editor.fill(caption, timeout=10_000)
        self._update(attempt_id, "awaiting_review", "Đã chọn video và điền caption. Kiểm tra tiến độ tải, tài khoản, quyền riêng tư và bấm Đăng trong TikTok Studio")

    def resolve(self, attempt_id: str) -> dict[str, Any]:
        with self._lock:
            active = self.active_attempt()
            if not active or active["id"] != attempt_id:
                raise TikTokBrowserError("Lượt chuẩn bị này không còn hoạt động")
            if active["state"] in BUSY_STATES or self._browser_lock.locked():
                raise TikTokBrowserError("Đang thao tác trên video. Hãy chờ trước khi kết thúc")
            with self._db() as db:
                db.execute("UPDATE attempts SET active=0, state='resolved', message=? WHERE id=?", (
                    "Người dùng đã kết thúc lượt chuẩn bị; VidTrans không xác nhận trạng thái đăng bài", attempt_id,
                ))
        return self.status()
