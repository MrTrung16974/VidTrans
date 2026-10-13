from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from domain.social_publishing import PublishResult, PublishStatus, PublisherCapabilities, SocialPlatform
from infrastructure.signed_media import SignedMediaUrlService


class ThreadsPublisherError(RuntimeError):
    pass


class ThreadsConfigurationError(ThreadsPublisherError):
    pass


class ThreadsAPIError(ThreadsPublisherError):
    pass


@dataclass(frozen=True)
class ThreadsPublisherConfig:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    scopes: tuple[str, ...] = ("threads_basic", "threads_content_publish")
    api_base: str = "https://graph.threads.net"
    authorize_url: str = "https://threads.net/oauth/authorize"
    token_url: str = "https://graph.threads.net/oauth/access_token"
    poll_attempts: int = 30
    poll_interval_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "ThreadsPublisherConfig":
        scopes = tuple(part.strip() for part in os.environ.get(
            "VIDTRANS_THREADS_SCOPES", "threads_basic,threads_content_publish"
        ).split(",") if part.strip())
        return cls(
            client_id=os.environ.get("VIDTRANS_THREADS_CLIENT_ID", "").strip(),
            client_secret=os.environ.get("VIDTRANS_THREADS_CLIENT_SECRET", "").strip(),
            redirect_uri=os.environ.get("VIDTRANS_THREADS_REDIRECT_URI", "").strip(),
            scopes=scopes,
            api_base=os.environ.get("VIDTRANS_THREADS_API_BASE", "https://graph.threads.net").rstrip("/"),
        )

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)


RequestFunction = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


def _default_request(method: str, url: str, headers: dict[str, str], body: bytes | None):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise ThreadsAPIError(f"Không kết nối được Threads: {exc.reason}") from exc


class ThreadsPublisher:
    platform = SocialPlatform.THREADS
    capabilities = PublisherCapabilities(
        supports_video=True, supports_text=True, supports_draft=False,
        supports_remote_status=True, caption_limit=500, schedule_horizon_days=365,
    )

    def __init__(self, auth_dir: Path, media_urls: SignedMediaUrlService, *,
                 config: ThreadsPublisherConfig | None = None,
                 requester: RequestFunction | None = None,
                 clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.auth_dir = Path(auth_dir)
        self.accounts_dir = self.auth_dir / "accounts"
        self.media_urls = media_urls
        self.config = config or ThreadsPublisherConfig.from_env()
        self._requester = requester or _default_request
        self._clock = clock
        self._sleeper = sleeper
        self._states: dict[str, float] = {}
        self._lock = threading.RLock()

    def authorization_url(self) -> str:
        self._require_configured()
        state = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._states = {key: expiry for key, expiry in self._states.items() if expiry > now}
            self._states[state] = now + 600
        query = urllib.parse.urlencode({
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": ",".join(self.config.scopes),
            "response_type": "code",
            "state": state,
        })
        return f"{self.config.authorize_url}?{query}"

    def exchange_code(self, code: str, state: str) -> dict[str, Any]:
        self._require_configured()
        with self._lock:
            expires = self._states.pop(state, 0)
        if not state or expires <= self._clock():
            raise ThreadsAPIError("Phiên kết nối Threads không hợp lệ hoặc đã hết hạn")
        short = self._request_json("POST", self.config.token_url, form={
            "client_id": self.config.client_id, "client_secret": self.config.client_secret,
            "grant_type": "authorization_code", "redirect_uri": self.config.redirect_uri, "code": code,
        })
        access_token = str(short.get("access_token") or "")
        if not access_token:
            raise ThreadsAPIError("Threads không trả về access token")
        long_token = self._get("/access_token", {
            "grant_type": "th_exchange_token", "client_secret": self.config.client_secret,
            "access_token": access_token,
        })
        access_token = str(long_token.get("access_token") or access_token)
        profile = self._get("/me", {"fields": "id,username", "access_token": access_token})
        account_id = str(profile.get("id") or "").strip()
        if not account_id:
            raise ThreadsAPIError("Threads không trả về định danh tài khoản")
        self._save_token(account_id, {
            "account_id": account_id, "username": str(profile.get("username") or ""),
            "access_token": access_token,
            "expires_at": self._clock() + float(long_token.get("expires_in") or 5184000),
        })
        return self.connection_status(account_id)

    def connection_status(self, account_id: str | None = None) -> dict[str, Any]:
        token = self._load_token(account_id) if account_id else self._first_token()
        connected = bool(token and token.get("access_token") and float(token.get("expires_at", 0)) > self._clock())
        return {
            "platform": self.platform.value,
            "configured": self.config.configured and self.media_urls.configured,
            "connected": connected,
            "account_id": token.get("account_id") if connected and token else account_id,
            "username": token.get("username") if connected and token else None,
            "expires_at": token.get("expires_at") if connected and token else None,
        }

    def accounts(self) -> list[dict[str, Any]]:
        try:
            paths = sorted(self.accounts_dir.glob("*.json"))
        except OSError:
            paths = []
        return [self.connection_status(str(token["account_id"])) for path in paths
                if (token := self._read_token(path)) and token.get("account_id")]

    def disconnect(self, account_id: str) -> None:
        self._token_path(account_id).unlink(missing_ok=True)

    def publish(self, request, *, on_checkpoint, on_progress=None):
        token = self._valid_token(request.account_id)
        video_url = self.media_urls.create_url(request.video_path, ttl_seconds=7200)
        created = self._post(f"/{token['account_id']}/threads", {
            "media_type": "VIDEO", "video_url": video_url, "text": request.caption,
            "access_token": token["access_token"],
        })
        container_id = str(created.get("id") or "")
        if not container_id:
            raise ThreadsAPIError("Threads không trả về container ID")
        on_checkpoint(container_id, None)
        if on_progress:
            on_progress(1, 2)
        detail = self._wait_ready(container_id, str(token["access_token"]))
        published = self._post(f"/{token['account_id']}/threads_publish", {
            "creation_id": container_id, "access_token": token["access_token"],
        })
        post_id = str(published.get("id") or "")
        if not post_id:
            raise ThreadsAPIError("Threads không trả về post ID")
        on_checkpoint(container_id, post_id)
        if on_progress:
            on_progress(2, 2)
        return PublishResult(status=PublishStatus.PUBLISHED, remote_media_id=container_id,
                             remote_post_id=post_id, status_detail=detail)

    def reconcile(self, *, remote_media_id, remote_post_id, account_id):
        token = self._valid_token(account_id)
        if remote_post_id:
            return PublishResult(status=PublishStatus.PUBLISHED,
                                 remote_media_id=remote_media_id, remote_post_id=remote_post_id)
        if not remote_media_id:
            raise ThreadsAPIError("Thiếu Threads container ID")
        detail = self._container_status(remote_media_id, str(token["access_token"]))
        status = str(detail.get("status") or "").upper()
        if status in {"ERROR", "EXPIRED"}:
            return PublishResult(status=PublishStatus.FAILED, remote_media_id=remote_media_id,
                                 status_detail=detail)
        if status != "FINISHED":
            return PublishResult(status=PublishStatus.PROCESSING_REMOTE,
                                 remote_media_id=remote_media_id, status_detail=detail)
        published = self._post(f"/{token['account_id']}/threads_publish", {
            "creation_id": remote_media_id, "access_token": token["access_token"],
        })
        post_id = str(published.get("id") or "")
        if not post_id:
            raise ThreadsAPIError("Threads không trả về post ID")
        return PublishResult(status=PublishStatus.PUBLISHED, remote_media_id=remote_media_id,
                             remote_post_id=post_id, status_detail=detail)

    def _wait_ready(self, container_id: str, access_token: str) -> dict[str, Any]:
        detail = {}
        for index in range(self.config.poll_attempts):
            detail = self._container_status(container_id, access_token)
            status = str(detail.get("status") or "").upper()
            if status == "FINISHED":
                return detail
            if status in {"ERROR", "EXPIRED"}:
                raise ThreadsAPIError(str(detail.get("error_message") or "Threads xử lý video thất bại"))
            if index + 1 < self.config.poll_attempts:
                self._sleeper(self.config.poll_interval_seconds)
        raise ThreadsAPIError("Threads chưa xử lý xong video; sẽ tiếp tục đối soát")

    def _container_status(self, container_id: str, access_token: str):
        return self._get(f"/{container_id}", {"fields": "status,error_message", "access_token": access_token})

    def _get(self, path: str, values: dict[str, Any]):
        return self._request_json("GET", f"{self.config.api_base}{path}?{urllib.parse.urlencode(values)}")

    def _post(self, path: str, values: dict[str, Any]):
        return self._request_json("POST", f"{self.config.api_base}{path}", form=values)

    def _request_json(self, method: str, url: str, *, form: dict[str, Any] | None = None):
        body = urllib.parse.urlencode(form).encode() if form is not None else None
        status, raw = self._requester(method, url, {"Content-Type": "application/x-www-form-urlencoded"} if body else {}, body)
        try:
            payload = json.loads(raw.decode()) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ThreadsAPIError(f"Threads trả về dữ liệu không hợp lệ (HTTP {status})") from exc
        if not isinstance(payload, dict) or not 200 <= status < 300 or payload.get("error"):
            error = payload.get("error") if isinstance(payload, dict) else {}
            message = error.get("message") if isinstance(error, dict) else None
            raise ThreadsAPIError(f"Threads API: {message or f'HTTP {status}'}")
        return payload

    def _valid_token(self, account_id: str | None):
        self._require_configured()
        if not account_id:
            raise ThreadsConfigurationError("Hãy chọn tài khoản Threads")
        token = self._load_token(account_id)
        if not token or not token.get("access_token"):
            raise ThreadsConfigurationError("Chưa kết nối tài khoản Threads")
        if float(token.get("expires_at", 0)) <= self._clock() + 300:
            raise ThreadsConfigurationError("Phiên Threads đã hết hạn. Hãy kết nối lại.")
        return token

    def _require_configured(self):
        if not self.config.configured:
            raise ThreadsConfigurationError("Chưa cấu hình Threads Developer App")
        if not self.media_urls.configured:
            raise ThreadsConfigurationError("Chưa cấu hình signed media public URL")

    def _token_path(self, account_id: str):
        if not re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", account_id):
            raise ThreadsConfigurationError("Định danh tài khoản Threads không hợp lệ")
        return self.accounts_dir / f"{account_id}.json"

    @staticmethod
    def _read_token(path: Path):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _load_token(self, account_id: str):
        return self._read_token(self._token_path(account_id))

    def _first_token(self):
        try:
            paths = sorted(self.accounts_dir.glob("*.json"))
        except OSError:
            return None
        return next((token for path in paths if (token := self._read_token(path))), None)

    def _save_token(self, account_id: str, payload: dict[str, Any]):
        path = self._token_path(account_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(path)

