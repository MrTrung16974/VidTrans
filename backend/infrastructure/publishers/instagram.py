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

from domain.social_publishing import (
    PublishRequest,
    PublishResult,
    PublishStatus,
    PublisherCapabilities,
    SocialPlatform,
)
from infrastructure.signed_media import SignedMediaUrlService


class InstagramPublisherError(RuntimeError):
    pass


class InstagramConfigurationError(InstagramPublisherError):
    pass


class InstagramAPIError(InstagramPublisherError):
    pass


@dataclass(frozen=True)
class InstagramPublisherConfig:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    scopes: tuple[str, ...] = (
        "instagram_business_basic",
        "instagram_business_content_publish",
    )
    api_version: str = "v24.0"
    graph_base: str = "https://graph.instagram.com"
    authorize_url: str = "https://www.instagram.com/oauth/authorize"
    token_url: str = "https://api.instagram.com/oauth/access_token"
    poll_attempts: int = 30
    poll_interval_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "InstagramPublisherConfig":
        scopes = tuple(
            part.strip()
            for part in os.environ.get(
                "VIDTRANS_INSTAGRAM_SCOPES",
                "instagram_business_basic,instagram_business_content_publish",
            ).split(",")
            if part.strip()
        )
        return cls(
            client_id=os.environ.get("VIDTRANS_INSTAGRAM_CLIENT_ID", "").strip(),
            client_secret=os.environ.get("VIDTRANS_INSTAGRAM_CLIENT_SECRET", "").strip(),
            redirect_uri=os.environ.get("VIDTRANS_INSTAGRAM_REDIRECT_URI", "").strip(),
            scopes=scopes,
            api_version=os.environ.get("VIDTRANS_INSTAGRAM_API_VERSION", "v24.0").strip(),
            graph_base=os.environ.get(
                "VIDTRANS_INSTAGRAM_GRAPH_BASE", "https://graph.instagram.com"
            ).rstrip("/"),
        )

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)


RequestFunction = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


def _default_request(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise InstagramAPIError(f"Không kết nối được Instagram: {exc.reason}") from exc


class InstagramPublisher:
    platform = SocialPlatform.INSTAGRAM
    capabilities = PublisherCapabilities(
        supports_video=True,
        supports_text=False,
        supports_draft=False,
        supports_remote_status=True,
        caption_limit=2200,
        schedule_horizon_days=365,
    )

    def __init__(
        self,
        auth_dir: Path,
        media_urls: SignedMediaUrlService,
        *,
        config: InstagramPublisherConfig | None = None,
        requester: RequestFunction | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        account_store=None,
    ) -> None:
        self.auth_dir = Path(auth_dir)
        self.accounts_dir = self.auth_dir / "accounts"
        self.config = config or InstagramPublisherConfig.from_env()
        self.media_urls = media_urls
        self._requester = requester or _default_request
        self._clock = clock
        self._sleeper = sleeper
        self.store = account_store
        self._states: dict[str, float] = {}
        self._lock = threading.RLock()

    def accept_consumed_state(self, state: str) -> None:
        """Restage a state already validated by the durable tenant OAuth session store."""
        if not state:
            raise InstagramAPIError("Phiên kết nối Instagram không hợp lệ")
        with self._lock:
            self._states[state] = self._clock() + 30

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
            "response_type": "code",
            "scope": ",".join(self.config.scopes),
            "state": state,
        })
        return f"{self.config.authorize_url}?{query}"

    def exchange_code(self, code: str, state: str) -> dict[str, Any]:
        self._require_configured()
        with self._lock:
            expires = self._states.pop(state, 0)
        if not state or expires <= self._clock():
            raise InstagramAPIError("Phiên kết nối Instagram không hợp lệ hoặc đã hết hạn")
        if not code:
            raise InstagramAPIError("Instagram không trả về mã xác thực")
        short_token = self._request_json(
            "POST",
            self.config.token_url,
            form={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": self.config.redirect_uri,
                "code": code,
            },
        )
        access_token = str(short_token.get("access_token") or "")
        if not access_token:
            raise InstagramAPIError("Instagram không trả về access token")
        long_token = self._graph_get(
            "/access_token",
            {
                "grant_type": "ig_exchange_token",
                "client_secret": self.config.client_secret,
                "access_token": access_token,
            },
            versioned=False,
        )
        access_token = str(long_token.get("access_token") or access_token)
        profile = self._graph_get(
            "/me", {"fields": "user_id,username", "access_token": access_token}
        )
        account_id = str(profile.get("user_id") or profile.get("id") or "").strip()
        if not account_id:
            raise InstagramAPIError("Instagram không trả về định danh tài khoản")
        token = {
            "account_id": account_id,
            "username": str(profile.get("username") or ""),
            "access_token": access_token,
            "expires_at": self._clock() + float(long_token.get("expires_in") or 5184000),
            "scope": ",".join(self.config.scopes),
        }
        self._save_token(account_id, token)
        return self.connection_status(account_id)

    def connection_status(self, account_id: str | None = None) -> dict[str, Any]:
        token = self._load_token(account_id) if account_id else self._first_token()
        connected = bool(
            token and token.get("access_token") and float(token.get("expires_at", 0)) > self._clock()
        )
        return {
            "platform": self.platform.value,
            "configured": self.config.configured and self.media_urls.configured,
            "connected": connected,
            "connection_id": token.get("connection_id") if connected and token else None,
            "account_id": token.get("account_id") if connected and token else account_id,
            "username": token.get("username") if connected and token else None,
            "expires_at": token.get("expires_at") if connected and token else None,
        }

    def accounts(self) -> list[dict[str, Any]]:
        if self.store is not None:
            return [
                self.connection_status(str(token["account_id"]))
                for token in self.store.all()
                if token.get("account_id")
            ]
        try:
            paths = sorted(self.accounts_dir.glob("*.json"))
        except OSError:
            paths = []
        result = []
        for path in paths:
            token = self._read_token(path)
            if token and token.get("account_id"):
                result.append(self.connection_status(str(token["account_id"])))
        return result

    def disconnect(self, account_id: str) -> None:
        if self.store is not None:
            self.store.delete(account_id)
            return
        self._token_path(account_id).unlink(missing_ok=True)

    def publish(self, request, *, on_checkpoint, on_progress=None):
        token = self._valid_token(request.account_id)
        account_id = str(token["account_id"])
        video_url = self.media_urls.create_url(request.video_path, ttl_seconds=7200)
        fields: dict[str, Any] = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": request.caption,
            "access_token": token["access_token"],
        }
        if "share_to_feed" in request.options:
            fields["share_to_feed"] = str(bool(request.options["share_to_feed"])).lower()
        created = self._graph_post(f"/{account_id}/media", fields)
        media_id = str(created.get("id") or "")
        if not media_id:
            raise InstagramAPIError("Instagram không trả về media container ID")
        on_checkpoint(media_id, None)
        if on_progress:
            on_progress(1, 2)
        detail = self._wait_until_ready(media_id, str(token["access_token"]))
        published = self._graph_post(
            f"/{account_id}/media_publish",
            {"creation_id": media_id, "access_token": token["access_token"]},
        )
        post_id = str(published.get("id") or "")
        if not post_id:
            raise InstagramAPIError("Instagram không trả về post ID")
        on_checkpoint(media_id, post_id)
        if on_progress:
            on_progress(2, 2)
        return PublishResult(
            status=PublishStatus.PUBLISHED,
            remote_media_id=media_id,
            remote_post_id=post_id,
            status_detail=detail,
        )

    def reconcile(self, *, remote_media_id, remote_post_id, account_id):
        token = self._valid_token(account_id)
        if remote_post_id:
            return PublishResult(
                status=PublishStatus.PUBLISHED,
                remote_media_id=remote_media_id,
                remote_post_id=remote_post_id,
            )
        if not remote_media_id:
            raise InstagramAPIError("Thiếu Instagram media container ID")
        detail = self._container_status(remote_media_id, str(token["access_token"]))
        status_code = str(detail.get("status_code") or "").upper()
        if status_code == "ERROR" or status_code == "EXPIRED":
            return PublishResult(
                status=PublishStatus.FAILED,
                remote_media_id=remote_media_id,
                status_detail=detail,
            )
        if status_code != "FINISHED":
            return PublishResult(
                status=PublishStatus.PROCESSING_REMOTE,
                remote_media_id=remote_media_id,
                status_detail=detail,
            )
        published = self._graph_post(
            f"/{token['account_id']}/media_publish",
            {"creation_id": remote_media_id, "access_token": token["access_token"]},
        )
        post_id = str(published.get("id") or "")
        if not post_id:
            raise InstagramAPIError("Instagram không trả về post ID")
        return PublishResult(
            status=PublishStatus.PUBLISHED,
            remote_media_id=remote_media_id,
            remote_post_id=post_id,
            status_detail=detail,
        )

    def _wait_until_ready(self, media_id: str, access_token: str) -> dict[str, Any]:
        detail: dict[str, Any] = {}
        for attempt in range(self.config.poll_attempts):
            detail = self._container_status(media_id, access_token)
            status = str(detail.get("status_code") or "").upper()
            if status == "FINISHED":
                return detail
            if status in {"ERROR", "EXPIRED"}:
                raise InstagramAPIError(
                    str(detail.get("status") or "Instagram xử lý video thất bại")
                )
            if attempt + 1 < self.config.poll_attempts:
                self._sleeper(self.config.poll_interval_seconds)
        raise InstagramAPIError("Instagram chưa xử lý xong video; sẽ tiếp tục đối soát")

    def _container_status(self, media_id: str, access_token: str) -> dict[str, Any]:
        return self._graph_get(
            f"/{media_id}",
            {"fields": "status_code,status", "access_token": access_token},
        )

    def _valid_token(self, account_id: str | None) -> dict[str, Any]:
        self._require_configured()
        if not account_id:
            raise InstagramConfigurationError("Hãy chọn tài khoản Instagram")
        token = self._load_token(account_id)
        if not token or not token.get("access_token"):
            raise InstagramConfigurationError("Chưa kết nối tài khoản Instagram")
        if float(token.get("expires_at", 0)) <= self._clock() + 300:
            raise InstagramConfigurationError(
                "Phiên Instagram đã hết hạn hoặc sắp hết hạn. Hãy kết nối lại."
            )
        return token

    def _graph_get(self, path: str, values: dict[str, Any], *, versioned: bool = True):
        prefix = f"/{self.config.api_version}" if versioned else ""
        url = f"{self.config.graph_base}{prefix}{path}?{urllib.parse.urlencode(values)}"
        return self._request_json("GET", url)

    def _graph_post(self, path: str, values: dict[str, Any]):
        url = f"{self.config.graph_base}/{self.config.api_version}{path}"
        return self._request_json("POST", url, form=values)

    def _request_json(
        self, method: str, url: str, *, form: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = urllib.parse.urlencode(form).encode("utf-8") if form is not None else None
        headers = {"Content-Type": "application/x-www-form-urlencoded"} if body else {}
        status, raw = self._requester(method, url, headers, body)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InstagramAPIError(f"Instagram trả về dữ liệu không hợp lệ (HTTP {status})") from exc
        if not isinstance(payload, dict):
            raise InstagramAPIError(f"Instagram trả về dữ liệu không hợp lệ (HTTP {status})")
        if not 200 <= status < 300 or payload.get("error"):
            error = payload.get("error") or {}
            message = error.get("message") or payload.get("error_message") or f"HTTP {status}"
            raise InstagramAPIError(f"Instagram API: {message}")
        return payload

    def _require_configured(self) -> None:
        if not self.config.configured:
            raise InstagramConfigurationError("Chưa cấu hình Instagram Developer App")
        if not self.media_urls.configured:
            raise InstagramConfigurationError("Chưa cấu hình signed media public URL")

    def _token_path(self, account_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", account_id):
            raise InstagramConfigurationError("Định danh tài khoản Instagram không hợp lệ")
        return self.accounts_dir / f"{account_id}.json"

    @staticmethod
    def _read_token(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _load_token(self, account_id: str) -> dict[str, Any] | None:
        if self.store is not None:
            return self.store.load(account_id)
        return self._read_token(self._token_path(account_id))

    def _first_token(self) -> dict[str, Any] | None:
        if self.store is not None:
            return next(iter(self.store.all()), None)
        try:
            paths = sorted(self.accounts_dir.glob("*.json"))
        except OSError:
            return None
        for path in paths:
            token = self._read_token(path)
            if token:
                return token
        return None

    def _save_token(self, account_id: str, payload: dict[str, Any]) -> None:
        if self.store is not None:
            self.store.save(account_id, payload)
            return
        path = self._token_path(account_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(path)

