"""Interactive TikTok preparation. Never clicks Publish or retries an upload.

The application runs one backend process and one dedicated Chromium profile.
Durable attempts reserve that profile until a human resolves the Studio page.
"""
from __future__ import annotations

import json
import logging
import os
import re
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
BUSY_STATES = {"queued", "opening", "uploading", "publishing"}
UPLOAD_WAIT_SECONDS = 15 * 60
UPLOAD_POLL_SECONDS = 2.0
CAPTION_SETTLE_SECONDS = 1.0
CAPTION_PREFILL_SETTLE_SECONDS = 3.0
CAPTION_FILL_ATTEMPTS = 3
CAPTION_STABLE_CHECKS = 5
# Reads TikTok Studio's upload card. The Post button stays disabled until the
# file is processed, so an enabled button (or an "Uploaded" label) means done.
UPLOAD_PROGRESS_JS = """() => {
    const text = document.body ? document.body.innerText : '';
    const button = document.querySelector('[data-e2e="post_video_button"]')
        || [...document.querySelectorAll('button')].find(b => /^(post|đăng)$/i.test(b.innerText.trim()));
    const enabled = Boolean(button) && !button.disabled
        && button.getAttribute('aria-disabled') !== 'true' && button.getAttribute('data-disabled') !== 'true';
    const percent = (text.match(/(\\d{1,3})\\s*%/) || [])[1];
    return {
        done: enabled || /(^|\\s)(uploaded|đã tải lên)(\\s|$)/i.test(text),
        failed: /(upload failed|couldn.t upload|tải lên không thành công|tải lên thất bại)/i.test(text),
        percent: percent === undefined ? null : Number(percent),
    };
}"""


LOGIN_LIMIT_MESSAGE = (
    "TikTok đang giới hạn số lần xác minh. Dừng gửi lại OTP hoặc đăng nhập lại; "
    "chờ TikTok cho phép thử lại. TikTok chưa cung cấp thời gian mở khóa. "
    "Giữ nguyên phiên và dùng Kiểm tra phiên để cập nhật trạng thái."
)


def login_is_limited(text: str) -> bool:
    return isinstance(text, str) and any(marker in text.casefold() for marker in (
        "maximum number of attempts reached", "too many attempts",
        "đã đạt số lần thử tối đa", "quá nhiều lần thử",
    ))


class TikTokBrowserError(RuntimeError):
    pass


def is_tiktok_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    return parsed.scheme == "https" and (
        hostname == "tiktok.com" or hostname.endswith(".tiktok.com")
    )


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
        self._session_checked_at = 0.0
        self._session_check_error = False
        self._login_limited = False
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, state TEXT NOT NULL, caption TEXT NOT NULL, message TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_browser ON attempts(active) WHERE active=1")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(attempts)")}
            if "file_sent" not in columns:
                # Only attempts that handed the file to TikTok can cause a duplicate post.
                db.execute("ALTER TABLE attempts ADD COLUMN file_sent INTEGER NOT NULL DEFAULT 0")
                db.execute("UPDATE attempts SET file_sent=1 WHERE state IN ('uploading','awaiting_review')")
            db.execute("UPDATE attempts SET state='needs_review', message=? WHERE active=1 AND state IN ('queued','opening','uploading','publishing')", (
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

    def _update(self, attempt_id: str, state: str, message: str, *, file_sent: bool = False):
        with self._db() as db:
            db.execute("UPDATE attempts SET state=?, message=?, file_sent=MAX(file_sent, ?) WHERE id=? AND active=1",
                       (state, message, int(file_sent), attempt_id))

    # JavaScript injected into every page to remove Chromium automation fingerprints.
    # navigator.webdriver is the primary signal TikTok checks before allowing QR login.
    _STEALTH_JS = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        Object.defineProperty(navigator, 'languages', {get: () => ['vi-VN', 'vi', 'zh-CN', 'zh', 'en-US', 'en']});
        window.chrome = {runtime: {}};
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({state: Notification.permission})
                : originalQuery(parameters);
    """

    def _with_browser(self, operation: Callable[[Any], Any]):
        from playwright.sync_api import sync_playwright
        with urllib.request.urlopen(f"{self.cdp_url.rstrip('/')}/json/version", timeout=3) as response:
            ws_url = json.load(response)["webSocketDebuggerUrl"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(ws_url, timeout=8_000)
            if not browser.contexts:
                raise TikTokBrowserError("Trình duyệt chưa sẵn sàng")

            # Inject stealth script on every new document to prevent bot detection.
            for context in browser.contexts:
                try:
                    context.add_init_script(self._STEALTH_JS)
                except Exception:
                    pass
                for page in context.pages:
                    try:
                        page.evaluate(self._STEALTH_JS)
                    except Exception:
                        pass

            return operation(browser)  # Disconnect only; preserve the user's Chromium.

    @staticmethod
    def _page_login_limited(page) -> bool:
        # TikTok renders some login and OTP challenges inside a cross-origin
        # iframe. Reading only the top-level body misses the visible rate-limit
        # message and leaves the login controls enabled, which encourages more
        # retries and can extend the temporary lock.
        for frame in page.frames:
            try:
                if login_is_limited(frame.locator("body").inner_text(timeout=2_000)):
                    return True
            except Exception:
                # A challenge frame can be replaced while it is being read.
                # Continue with the remaining frames instead of treating that
                # transient navigation as a successful login check.
                continue
        return False

    def _read_session(self, browser):
        present = has_session(browser.contexts[0].cookies())
        pages = [p for p in browser.contexts[0].pages if is_tiktok_url(p.url)]
        self._login_limited = not present and any(self._page_login_limited(p) for p in pages)
        return present

    def _guard_login(self, page):
        self._login_limited = self._page_login_limited(page)
        if self._login_limited:
            raise TikTokBrowserError(LOGIN_LIMIT_MESSAGE)

    @staticmethod
    def _reset_tiktok_login_state(context, page) -> None:
        """Discard an account-bound OTP challenge before opening QR login.

        TikTok keeps the verification modal in cookies and origin storage. A
        plain navigation to ``loginType=qrCode`` can therefore leave the old
        OTP screen mounted. The browser sidecar is dedicated to TikTok, so it
        is safe to clear its context when the user explicitly chooses QR.
        """
        try:
            cdp = context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Network.clearBrowserCookies")
            cdp.send("Network.clearBrowserCache")
            cdp.send(
                "Storage.clearDataForOrigin",
                {"origin": "https://www.tiktok.com", "storageTypes": "all"},
            )
            cdp.detach()
        except Exception:
            # Older Chromium builds may not expose every storage command.
            # Continue with the Playwright/browser-side cleanup below.
            pass
        try:
            page.evaluate(
                """async () => {
                    localStorage.clear();
                    sessionStorage.clear();
                    if (globalThis.caches) {
                        for (const key of await caches.keys()) await caches.delete(key);
                    }
                    if (indexedDB.databases) {
                        for (const db of await indexedDB.databases()) {
                            if (db.name) indexedDB.deleteDatabase(db.name);
                        }
                    }
                }"""
            )
        except Exception:
            # Storage may be unavailable while TikTok replaces the challenge
            # frame. Cookies are the authoritative reset and still proceed.
            pass
        context.clear_cookies()

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        available = False
        try:
            with urllib.request.urlopen(f"{self.cdp_url.rstrip('/')}/json/version", timeout=2) as response:
                available = response.status == 200
        except (OSError, ValueError):
            pass
        # Poll real cookies after QR confirmation, with bounded CDP traffic.
        check_due = refresh or time.monotonic() - self._session_checked_at >= 5
        if check_due and available and self._browser_lock.acquire(blocking=False):
            try:
                self._session_present = self._with_browser(self._read_session)
                self._session_check_error = False
            except Exception:
                # A transport failure does not mean TikTok rejected the login.
                self._session_check_error = True
            finally:
                self._session_checked_at = time.monotonic()
                self._browser_lock.release()
        if not available:
            self._session_present = False
        return {
            "login_state": "rate_limited" if self._login_limited else "success" if self._session_present else "pending",
            "available": available,
            "session_present": self._session_present,
            "session_check_error": self._session_check_error,
            "browser_url": self.public_url if available else None,
            "attempt": self.active_attempt(),
            "message": "Trình duyệt chưa sẵn sàng" if not available else (
                "Chưa kiểm tra được phiên TikTok. Bấm Kiểm tra phiên để thử lại; đây không phải thông báo đăng nhập thất bại."
                if self._session_check_error else
                "Đã tìm thấy phiên đăng nhập · Kiểm tra tài khoản trong TikTok Studio" if self._session_present
                else LOGIN_LIMIT_MESSAGE if self._login_limited
                else "Chưa thấy phiên TikTok. Quét QR và xác nhận trên điện thoại; trạng thái sẽ tự cập nhật."
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

    def restart_login(self, method: str = "default") -> dict[str, Any]:
        """Open TikTok's login page.

        ``method`` controls which login tab is pre-selected:
        - ``"default"``  – navigate to /login and show whatever TikTok defaults to
        - ``"email"``    – click the "Use phone / email / username" tab automatically
        - ``"qr"``       – navigate to /login?loginType=qrCode
        """
        with self._lock:
            if self.active_attempt():
                raise TikTokBrowserError("Hãy kết thúc lượt chuẩn bị bài hiện tại trước khi mở lại đăng nhập")
            if not self._browser_lock.acquire(blocking=False):
                raise TikTokBrowserError("Trình duyệt đang bận. Hãy thử lại sau")
            try:
                def open_login(browser):
                    page = self._page(browser)
                    # An OTP limit applies to the current email/phone challenge.
                    # Keep QR recovery available so the user can leave that
                    # challenge without submitting another code attempt.
                    if method != "qr":
                        self._guard_login(page)
                    if method == "qr":
                        self._reset_tiktok_login_state(browser.contexts[0], page)
                        page.goto("about:blank", wait_until="domcontentloaded", timeout=10_000)
                        url = "https://www.tiktok.com/login?loginType=qrCode"
                    elif method == "email":
                        url = "https://www.tiktok.com/login?loginType=phoneOrEmail"
                    else:
                        url = "https://www.tiktok.com/login"
                    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    page.bring_to_front()

                    if method == "qr":
                        # Some TikTok builds ignore loginType on the first
                        # navigation. Select the rendered QR option when the
                        # login chooser is shown.
                        qr_selector = (
                            "a[href*='loginType=qrCode'], "
                            "[data-e2e='login-qr-code'], "
                            "button:has-text('Use QR code'), "
                            "a:has-text('Use QR code'), "
                            "button:has-text('Sử dụng mã QR'), "
                            "a:has-text('Sử dụng mã QR')"
                        )
                        try:
                            qr = page.locator(qr_selector).first
                            if qr.is_visible(timeout=5_000):
                                qr.click()
                                page.wait_for_timeout(1_000)
                        except Exception:
                            # The query parameter already opens QR on the
                            # common layout, so absence of the chooser is fine.
                            pass
                        self._login_limited = self._page_login_limited(page)

                    if method == "email":
                        # Try to click the "Email / Phone / Username" tab if still on chooser page
                        try:
                            tab_selector = (
                                "a[href*='loginType=phoneOrEmail'], "
                                "[data-e2e='login-phone-email-tab'], "
                                "button:has-text('phone'), "
                                "a:has-text('phone')"
                            )
                            tab = page.locator(tab_selector).first
                            if tab.is_visible(timeout=3_000):
                                tab.click()
                                page.wait_for_load_state("domcontentloaded", timeout=10_000)
                        except Exception:
                            pass  # Non-fatal — the page URL already has loginType=phoneOrEmail

                self._with_browser(open_login)
            except TikTokBrowserError:
                raise
            except Exception as exc:
                raise TikTokBrowserError(
                    "Không mở được trang đăng nhập TikTok. Kiểm tra kết nối trình duyệt trên máy chủ"
                ) from exc
            finally:
                self._browser_lock.release()
        return self.status(refresh=True)

    def login_with_email(self, email: str, password: str) -> dict[str, Any]:
        """Automate TikTok login with email/phone + password via Playwright.

        This method is designed for VPS deployments where QR code scanning is
        blocked by TikTok's IP policies.  It:

        1. Navigates to the email/phone login page.
        2. Fills in the identifier and password fields.
        3. Clicks the login button.
        4. Waits up to 20 s for a session cookie to appear.
        5. Returns status; if a CAPTCHA or 2FA screen appears the user must
           complete it manually in the VNC browser — the method returns
           ``login_pending`` status so the frontend can prompt accordingly.
        """
        if not email or not password:
            raise TikTokBrowserError("Email/số điện thoại và mật khẩu không được để trống")
        with self._lock:
            if self.active_attempt():
                raise TikTokBrowserError("Hãy kết thúc lượt chuẩn bị bài hiện tại trước khi đăng nhập lại")
            if not self._browser_lock.acquire(blocking=False):
                raise TikTokBrowserError("Trình duyệt đang bận. Hãy thử lại sau")
            login_result: dict[str, Any] = {}
            try:
                def do_login(browser):
                    page = self._page(browser)
                    self._guard_login(page)
                    page.goto(
                        "https://www.tiktok.com/login?loginType=phoneOrEmail",
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    page.bring_to_front()

                    # --- Step 1: Switch to Email sub-tab if we are on a phone tab ---
                    try:
                        email_tab = page.locator(
                            "a:has-text('Email'), [data-e2e='login-email-tab'], "
                            "button:has-text('Email')"
                        ).first
                        if email_tab.is_visible(timeout=3_000):
                            email_tab.click()
                            page.wait_for_timeout(600)
                    except Exception:
                        pass

                    # --- Step 2: Fill email/phone ---
                    identifier_input = page.locator(
                        "input[name='email'], input[type='email'], "
                        "input[placeholder*='mail'], input[placeholder*='phone'], "
                        "input[placeholder*='Email'], "
                        "[data-e2e='login-email-or-phone-input'] input, "
                        "[data-e2e='login-user-input'] input"
                    ).first
                    identifier_input.wait_for(state="visible", timeout=10_000)
                    identifier_input.fill("")
                    identifier_input.type(email, delay=80)
                    page.wait_for_timeout(400)

                    # --- Step 3: Fill password ---
                    password_input = page.locator(
                        "input[type='password'], [data-e2e='login-password'] input"
                    ).first
                    password_input.wait_for(state="visible", timeout=8_000)
                    password_input.fill("")
                    password_input.type(password, delay=60)
                    page.wait_for_timeout(400)

                    # --- Step 4: Submit ---
                    login_btn = page.locator(
                        "button[type='submit'], [data-e2e='login-button'], "
                        "button:has-text('Log in'), button:has-text('Đăng nhập')"
                    ).first
                    login_btn.wait_for(state="visible", timeout=5_000)
                    login_btn.click()

                    # --- Step 5: Wait for session cookie or error ---
                    deadline = time.monotonic() + 20
                    session_found = False
                    while time.monotonic() < deadline:
                        cookies = browser.contexts[0].cookies()
                        if has_session(cookies):
                            session_found = True
                            break
                        if self._page_login_limited(page):
                            self._login_limited = True
                            break
                        page.wait_for_timeout(800)

                    login_result["session_found"] = session_found
                    login_result["current_url"] = page.url

                self._with_browser(do_login)
            except TikTokBrowserError:
                raise
            except Exception as exc:
                raise TikTokBrowserError(
                    f"Không thể tự động điền thông tin đăng nhập: {exc}. "
                    "Hãy đăng nhập thủ công trong cửa sổ trình duyệt bên dưới."
                ) from exc
            finally:
                self._browser_lock.release()

        current_url = login_result.get("current_url", "")
        session_found = login_result.get("session_found", False)

        base = self.status(refresh=True)
        if session_found:
            self._session_present = True
            base["message"] = "Đăng nhập email thành công · Đã tìm thấy phiên TikTok"
            base["login_state"] = "success"
        elif self._login_limited:
            base["message"] = LOGIN_LIMIT_MESSAGE
            base["login_state"] = "rate_limited"
        elif "captcha" in current_url.lower() or "verify" in current_url.lower():
            base["message"] = (
                "TikTok yêu cầu xác minh thêm (CAPTCHA/2FA). "
                "Hoàn tất thủ công trong cửa sổ trình duyệt bên dưới, rồi bấm 'Kiểm tra phiên'."
            )
            base["login_state"] = "captcha_required"
        elif "login" in current_url:
            base["message"] = (
                "TikTok chưa xác nhận đăng nhập. Kiểm tra thông báo trong khung trình duyệt "
                "và hoàn tất xác minh nếu được yêu cầu; không gửi lại liên tục."
            )
            base["login_state"] = "failed"
        else:
            base["message"] = (
                "Đang chờ xác nhận phiên. Bấm 'Kiểm tra phiên' sau vài giây, "
                "hoặc hoàn tất CAPTCHA thủ công nếu cần."
            )
            base["login_state"] = "pending"
        return base

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
                    db.execute("INSERT INTO attempts (id, job_id, state, caption, message, active, created_at) VALUES (?,?,?,?,?,1,?)", (
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
        except TikTokBrowserError as exc:
            logger.info("TikTok preparation needs review: %s", exc)
            self._update(attempt_id, "needs_review", str(exc))
        except Exception as exc:
            logger.warning("TikTok preparation stopped: %s", type(exc).__name__)
            self._update(attempt_id, "needs_review", "Chưa thể hoàn tất tự động. Mở TikTok Studio để kiểm tra đăng nhập, xác minh hoặc video đang tải. Không tự động thử lại.")
        finally:
            self._browser_lock.release()

    def _fill_upload(self, browser, attempt_id: str, video_path: Path, caption: str):
        page = self._page(browser)
        page.bring_to_front()
        if not has_session(browser.contexts[0].cookies()):
            self._session_present = False
            self._update(attempt_id, "needs_login", "Đăng nhập hoặc hoàn tất xác minh trong TikTok Studio, rồi kết thúc lượt này và chuẩn bị lại")
            return
        self._session_present = True

        # Keep a clean Studio upload page warm between jobs. Reloading the page
        # here adds several seconds on a VPS and can also trigger TikTok's
        # before-unload prompt. A page is reused only when it is already on the
        # exact upload route and exposes the file input; otherwise navigate once.
        current_path = urllib.parse.urlsplit(page.url).path
        inputs = page.locator('input[type="file"]')
        reuse_ready_page = (
            is_tiktok_url(page.url)
            and current_path.startswith("/tiktokstudio/upload")
            and inputs.count() == 1
        )
        if not reuse_ready_page:
            page.goto(STUDIO_URL, wait_until="domcontentloaded", timeout=30_000)
            inputs = page.locator('input[type="file"]')

        inputs.first.wait_for(state="attached", timeout=20_000)
        if not is_tiktok_url(page.url) or not urllib.parse.urlsplit(page.url).path.startswith("/tiktokstudio/upload") or inputs.count() != 1:
            raise TikTokBrowserError("Không nhận diện được ô tải video TikTok")
        # Marked before the transfer: a half-sent file may still create a draft on TikTok.
        size_mb = video_path.stat().st_size / (1024 * 1024)
        self._update(
            attempt_id,
            "uploading",
            f"Đang chuyển {video_path.name} ({size_mb:.1f} MB) sang TikTok · không cần chọn file thủ công",
            file_sent=True,
        )
        # Both containers mount outputs at the same absolute path; no public file URL.
        inputs.set_input_files(str(video_path), timeout=60_000)
        editor = page.locator('[contenteditable="true"][role="textbox"]:visible')
        editor.first.wait_for(state="visible", timeout=60_000)
        if not is_tiktok_url(page.url) or editor.count() != 1:
            raise TikTokBrowserError("Không nhận diện được ô caption duy nhất")
        uploaded = self._wait_for_upload(page, attempt_id)
        # TikTok prefills the caption with the filename while processing, so the
        # caption is written only after its final post-upload render has had time
        # to run. The editor can still be replaced later, so verify it repeatedly.
        page.wait_for_timeout(CAPTION_PREFILL_SETTLE_SECONDS * 1000)
        self._fill_caption(page, caption)
        if uploaded:
            message = "Video đã tải lên xong và caption đã được điền. Kiểm tra tài khoản, quyền riêng tư và bấm Đăng trong TikTok Studio"
        else:
            message = "Đã chọn video và điền caption nhưng TikTok vẫn đang xử lý. Chờ tải xong, kiểm tra caption và bấm Đăng trong TikTok Studio"
        self._update(attempt_id, "awaiting_review", message)

    def _wait_for_upload(self, page, attempt_id: str) -> bool:
        deadline = time.monotonic() + UPLOAD_WAIT_SECONDS
        last_percent = None
        while time.monotonic() < deadline:
            if not is_tiktok_url(page.url):
                raise TikTokBrowserError("Trang TikTok đã chuyển hướng trong lúc tải video")
            try:
                progress = page.evaluate(UPLOAD_PROGRESS_JS) or {}
            except Exception:
                progress = {}
            if progress.get("failed"):
                raise TikTokBrowserError("TikTok báo tải video thất bại")
            if progress.get("done"):
                return True
            percent = progress.get("percent")
            if percent is not None and percent != last_percent:
                last_percent = percent
                self._update(attempt_id, "uploading", f"Đang tải video lên TikTok · {percent}%. Hãy giữ nguyên trang")
            page.wait_for_timeout(UPLOAD_POLL_SECONDS * 1000)
        return False

    @staticmethod
    def _fill_caption(page, caption: str) -> None:
        expected = " ".join(caption.split())
        selector = '[contenteditable="true"][role="textbox"]:visible'
        for _ in range(CAPTION_FILL_ATTEMPTS):
            editor = page.locator(selector)
            if not is_tiktok_url(page.url) or editor.count() != 1:
                raise TikTokBrowserError("Không nhận diện được ô caption duy nhất")
            editor.first.click(timeout=10_000)
            editor.first.press("Control+A", timeout=5_000)
            editor.first.press("Backspace", timeout=5_000)
            page.keyboard.insert_text(caption)
            page.keyboard.press("Tab")
            stable = True
            for _ in range(CAPTION_STABLE_CHECKS):
                page.wait_for_timeout(CAPTION_SETTLE_SECONDS * 1000)
                try:
                    current = page.locator(selector)
                    if current.count() != 1 or " ".join(current.first.inner_text(timeout=5_000).split()) != expected:
                        stable = False
                        break
                except Exception:
                    stable = False
                    break
            if stable:
                return
        raise TikTokBrowserError("TikTok không giữ caption đã điền")

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

    def publish(self, attempt_id: str) -> dict[str, Any]:
        with self._lock:
            active = self.active_attempt()
            if not active or active["id"] != attempt_id:
                raise TikTokBrowserError("Lượt chuẩn bị này không còn hoạt động")
            if active["state"] != "awaiting_review":
                raise TikTokBrowserError("Chỉ có thể đăng sau khi video và caption đã chuẩn bị xong")
            if not self._browser_lock.acquire(blocking=False):
                raise TikTokBrowserError("Trình duyệt đang bận. Hãy thử lại sau")
            self._update(attempt_id, "publishing", "Đang gửi yêu cầu đăng bài tới TikTok")
        try:
            self._with_browser(self._click_publish)
            self._update(
                attempt_id,
                "publish_submitted",
                "Đã bấm Đăng trên TikTok. Kiểm tra thông báo kết quả trong TikTok Studio rồi kết thúc lượt chuẩn bị",
            )
        except TikTokBrowserError:
            self._update(attempt_id, "needs_review", "Chưa thể bấm Đăng. Kiểm tra nút Post và các yêu cầu còn thiếu trong TikTok Studio")
            raise
        except Exception as exc:
            self._update(attempt_id, "needs_review", "Không xác định được TikTok đã nhận thao tác Đăng hay chưa. Kiểm tra bài trước khi thao tác tiếp")
            raise TikTokBrowserError(
                "Không xác định được TikTok đã nhận thao tác Đăng hay chưa. Kiểm tra bài trước khi thao tác tiếp"
            ) from exc
        finally:
            self._browser_lock.release()
        return self.status()

    def _click_publish(self, browser) -> None:
        page = self._page(browser)
        page.bring_to_front()
        if not is_tiktok_url(page.url) or not urllib.parse.urlsplit(page.url).path.startswith("/tiktokstudio/upload"):
            raise TikTokBrowserError("TikTok Studio không còn ở trang đăng video")
        button = page.locator('[data-e2e="post_video_button"]:visible')
        if button.count() == 0:
            button = page.get_by_role("button", name=re.compile(r"^(post|đăng)$", re.IGNORECASE))
        if button.count() != 1 or not button.first.is_enabled():
            raise TikTokBrowserError("Nút Post chưa sẵn sàng. Kiểm tra quyền riêng tư và các mục bắt buộc")
        button.first.click(timeout=10_000)
