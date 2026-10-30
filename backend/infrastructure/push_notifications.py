"""Push "job finished" to the owner's phones through FCM HTTP v1.

Configured with ``VIDTRANS_FCM_CREDENTIALS_FILE`` (a Firebase service-account
JSON). Without it every call is a no-op. Sending happens on a small background
pool so a slow or unreachable FCM never delays a pipeline worker, and failures
are logged, never raised.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from infrastructure.device_store import DeviceStore

logger = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
NOTIFIED_STATUSES = {"completed", "failed"}
# Errors after which a token will never work again.
DEAD_TOKEN_ERRORS = {"UNREGISTERED", "NOT_FOUND"}


class FcmError(RuntimeError):
    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class FcmSender:
    """Minimal FCM HTTP v1 client: service-account JWT -> OAuth token -> messages:send."""

    def __init__(self, credentials: dict[str, Any], *, timeout: float = 10.0,
                 urlopen: Callable[..., Any] = urllib.request.urlopen) -> None:
        self.project_id = str(credentials["project_id"])
        self._client_email = str(credentials["client_email"])
        self._private_key = str(credentials["private_key"])
        self._token_uri = str(credentials.get("token_uri") or "https://oauth2.googleapis.com/token")
        self._timeout = timeout
        self._urlopen = urlopen
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._expires_at = 0.0

    @classmethod
    def from_env(cls) -> "FcmSender | None":
        path = os.environ.get("VIDTRANS_FCM_CREDENTIALS_FILE", "").strip()
        if not path:
            return None
        try:
            return cls(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError) as exc:
            logger.error("Push notifications disabled: cannot read FCM credentials %s: %s", path, exc)
            return None

    def _assertion(self, now: int) -> str:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = _b64url(json.dumps({
            "iss": self._client_email, "scope": FCM_SCOPE, "aud": self._token_uri,
            "iat": now, "exp": now + 3600,
        }).encode())
        key = serialization.load_pem_private_key(self._private_key.encode(), password=None)
        signature = key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{claims}.{_b64url(signature)}"

    def access_token(self) -> str:
        with self._lock:
            now = time.time()
            if self._access_token and now < self._expires_at - 60:
                return self._access_token
            body = urllib.parse.urlencode({
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": self._assertion(int(now)),
            }).encode()
            request = urllib.request.Request(
                self._token_uri, data=body, method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with self._urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self._access_token = str(payload["access_token"])
            self._expires_at = now + float(payload.get("expires_in", 3600))
            return self._access_token

    def send(self, token: str, *, title: str, body: str, data: dict[str, str]) -> None:
        message = {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body},
                "data": data,
                "android": {"priority": "high"},
                "apns": {"payload": {"aps": {"sound": "default"}}},
            }
        }
        request = urllib.request.Request(
            f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send",
            data=json.dumps(message).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {self.access_token()}", "Content-Type": "application/json"},
        )
        try:
            with self._urlopen(request, timeout=self._timeout) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            raise FcmError(f"FCM {exc.code}: {raw[:300]}", error_code=_fcm_error_code(raw, exc.code)) from exc


def _fcm_error_code(raw: str, status: int) -> str | None:
    try:
        error = json.loads(raw).get("error", {})
    except ValueError:
        return "NOT_FOUND" if status == 404 else None
    for detail in error.get("details", []) or []:
        if detail.get("errorCode"):
            return str(detail["errorCode"])
    return str(error.get("status") or "") or None


def job_message(job_id: str, status: str, job: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    name = str(job.get("filename") or job.get("title") or job.get("source_url") or "Video của bạn")
    if status == "completed":
        title, body = "Đã xử lý xong", f"{name} đã sẵn sàng."
    else:
        title, body = "Xử lý thất bại", f"{name} gặp lỗi. Mở app để xem chi tiết hoặc chạy lại."
    return title, body, {"type": "job_status", "job_id": job_id, "status": status}


class JobPushNotifier:
    """Listens to job status writes; pushes each job outcome once per attempt."""

    def __init__(self, devices: DeviceStore, sender: FcmSender | None,
                 get_job: Callable[[str], dict[str, Any] | None], *, workers: int = 2) -> None:
        self._devices = devices
        self._sender = sender
        self._get_job = get_job
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vidtrans-push") if sender else None

    @property
    def enabled(self) -> bool:
        return self._sender is not None

    def on_status(self, job_id: str, status: str) -> None:
        """Called from worker threads right after a job's status was written."""
        if self._pool is None:
            return
        if status == "queued":
            # A retry starts a new attempt whose outcome deserves its own push.
            self._pool.submit(self._safely, self._devices.reset_deliveries, job_id)
        elif status in NOTIFIED_STATUSES:
            self._pool.submit(self._safely, self.deliver, job_id, status)

    def _safely(self, function: Callable[..., Any], *args: Any) -> None:
        try:
            function(*args)
        except Exception:
            logger.exception("Push notification task failed")

    def deliver(self, job_id: str, status: str) -> int:
        """Send synchronously; returns the number of devices reached."""
        if self._sender is None:
            return 0
        job = self._get_job(job_id)
        owner = str((job or {}).get("owner_user_id") or "")
        if not job or not owner or str(job.get("status")) != status:
            return 0
        tokens = self._devices.tokens_for_user(owner)
        if not tokens or not self._devices.claim_delivery(job_id, status):
            return 0
        title, body, data = job_message(job_id, status, job)
        sent = 0
        for token in tokens:
            try:
                self._sender.send(token, title=title, body=body, data=data)
                sent += 1
            except FcmError as exc:
                if exc.error_code in DEAD_TOKEN_ERRORS:
                    self._devices.delete_token(token)
                else:
                    logger.warning("FCM push for job %s failed: %s", job_id, exc)
            except (OSError, ValueError) as exc:
                logger.warning("FCM push for job %s failed: %s", job_id, exc)
        return sent

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
