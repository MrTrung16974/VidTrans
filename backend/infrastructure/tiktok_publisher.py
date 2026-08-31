from __future__ import annotations

import json
import mimetypes
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


MIB = 1024 * 1024
MAX_VIDEO_SIZE = 4 * 1024 * MIB
MIN_CHUNK_SIZE = 5 * MIB
MAX_CHUNK_SIZE = 64 * MIB
ALLOWED_PRIVACY_LEVELS = {
    "PUBLIC_TO_EVERYONE",
    "MUTUAL_FOLLOW_FRIENDS",
    "FOLLOWER_OF_CREATOR",
    "SELF_ONLY",
}


class TikTokPublisherError(RuntimeError):
    """A user-safe TikTok connection or publishing error."""


class TikTokConfigurationError(TikTokPublisherError):
    pass


class TikTokAPIError(TikTokPublisherError):
    pass


@dataclass(frozen=True)
class TikTokPublisherConfig:
    client_key: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    scopes: tuple[str, ...] = ("video.publish",)
    api_base: str = "https://open.tiktokapis.com"
    authorize_url: str = "https://www.tiktok.com/v2/auth/authorize/"

    @classmethod
    def from_env(cls) -> "TikTokPublisherConfig":
        scopes = tuple(
            part.strip()
            for part in os.environ.get(
                "VIDTRANS_TIKTOK_SCOPES",
                "video.publish",
            ).split(",")
            if part.strip()
        )
        return cls(
            client_key=os.environ.get("VIDTRANS_TIKTOK_CLIENT_KEY", "").strip(),
            client_secret=os.environ.get("VIDTRANS_TIKTOK_CLIENT_SECRET", "").strip(),
            redirect_uri=os.environ.get("VIDTRANS_TIKTOK_REDIRECT_URI", "").strip(),
            scopes=scopes,
            api_base=os.environ.get(
                "VIDTRANS_TIKTOK_API_BASE",
                "https://open.tiktokapis.com",
            ).rstrip("/"),
        )

    @property
    def configured(self) -> bool:
        return bool(self.client_key and self.client_secret and self.redirect_uri)


@dataclass(frozen=True)
class UploadPlan:
    video_size: int
    chunk_size: int
    total_chunk_count: int


def create_upload_plan(video_size: int) -> UploadPlan:
    if video_size <= 0:
        raise ValueError("Video TikTok không được rỗng")
    if video_size > MAX_VIDEO_SIZE:
        raise ValueError("Video TikTok không được vượt quá 4 GB")
    if video_size <= MAX_CHUNK_SIZE:
        return UploadPlan(video_size, video_size, 1)

    # TikTok allows the last PUT to contain the regular chunk plus the tail.
    # Consequently floor(size/chunk_size), rather than ceil(), is the value
    # expected by total_chunk_count in the FILE_UPLOAD init request.
    count = max(1, video_size // MAX_CHUNK_SIZE)
    return UploadPlan(video_size, MAX_CHUNK_SIZE, count)


RequestFunction = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


def _default_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise TikTokAPIError(f"Không kết nối được TikTok: {exc.reason}") from exc


class TikTokPublisher:
    def __init__(
        self,
        auth_dir: Path,
        *,
        config: TikTokPublisherConfig | None = None,
        requester: RequestFunction | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.auth_dir = auth_dir
        self.token_path = auth_dir / "token.json"
        self.config = config or TikTokPublisherConfig.from_env()
        self._requester = requester or _default_request
        self._clock = clock
        self._states: dict[str, float] = {}
        self._lock = threading.RLock()

    def connection_status(self) -> dict[str, Any]:
        token = self._load_token()
        now = self._clock()
        connected = bool(
            token
            and token.get("access_token")
            and float(token.get("refresh_expires_at", now + 1)) > now
        )
        return {
            "configured": self.config.configured,
            "connected": connected,
            "open_id": token.get("open_id") if connected and token else None,
            "scope": token.get("scope", "") if connected and token else "",
            "access_expires_at": token.get("expires_at") if connected and token else None,
            "message": self._status_message(connected),
        }

    def _status_message(self, connected: bool) -> str:
        if connected:
            return "TikTok đã kết nối, sẵn sàng tự động đăng video"
        if not self.config.configured:
            return "Chưa cấu hình TikTok Developer App trên máy chủ"
        return "Chưa kết nối tài khoản TikTok"

    def authorization_url(self) -> str:
        self._require_configured()
        state = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._states = {key: expiry for key, expiry in self._states.items() if expiry > now}
            self._states[state] = now + 600
        query = urllib.parse.urlencode(
            {
                "client_key": self.config.client_key,
                "scope": ",".join(self.config.scopes),
                "response_type": "code",
                "redirect_uri": self.config.redirect_uri,
                "state": state,
            }
        )
        return f"{self.config.authorize_url}?{query}"

    def exchange_code(self, code: str, state: str) -> dict[str, Any]:
        self._require_configured()
        if not code:
            raise TikTokAPIError("TikTok không trả về mã xác thực")
        with self._lock:
            expires_at = self._states.pop(state, 0)
        if not state or expires_at <= self._clock():
            raise TikTokAPIError("Phiên kết nối TikTok không hợp lệ hoặc đã hết hạn")
        token = self._post_form(
            "/v2/oauth/token/",
            {
                "client_key": self.config.client_key,
                "client_secret": self.config.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.config.redirect_uri,
            },
        )
        self._save_token(self._normalize_token(token))
        return self.connection_status()

    def disconnect(self) -> None:
        with self._lock:
            self.token_path.unlink(missing_ok=True)

    def creator_info(self) -> dict[str, Any]:
        token = self._valid_access_token()
        payload = self._post_json(
            "/v2/post/publish/creator_info/query/",
            {},
            token,
        )
        return dict(payload.get("data") or {})

    def publish(
        self,
        video_path: Path,
        title: str,
        *,
        privacy_level: str = "SELF_ONLY",
        disable_comment: bool = False,
        disable_duet: bool = False,
        disable_stitch: bool = False,
    ) -> dict[str, Any]:
        if privacy_level not in ALLOWED_PRIVACY_LEVELS:
            raise TikTokPublisherError("Mức quyền riêng tư TikTok không hợp lệ")
        if not video_path.is_file():
            raise TikTokPublisherError("Không tìm thấy video đã dựng để đăng TikTok")
        title = " ".join((title or "").split()).strip()
        if not title:
            raise TikTokPublisherError("Tiêu đề TikTok đã tạo đang rỗng")
        if len(title.encode("utf-16-le")) // 2 > 2200:
            raise TikTokPublisherError("Tiêu đề TikTok vượt quá giới hạn 2.200 ký tự")

        creator = self.creator_info()
        privacy_options = creator.get("privacy_level_options") or []
        if privacy_level not in privacy_options:
            raise TikTokPublisherError(
                "Tài khoản TikTok hiện không hỗ trợ mức hiển thị đã chọn. "
                "Hãy chọn một mức quyền riêng tư khác."
            )
        disable_comment = bool(disable_comment or creator.get("comment_disabled"))
        disable_duet = bool(disable_duet or creator.get("duet_disabled"))
        disable_stitch = bool(disable_stitch or creator.get("stitch_disabled"))

        plan = create_upload_plan(video_path.stat().st_size)
        token = self._valid_access_token()
        init_payload = {
            "post_info": {
                "title": title,
                "privacy_level": privacy_level,
                "disable_comment": bool(disable_comment),
                "disable_duet": bool(disable_duet),
                "disable_stitch": bool(disable_stitch),
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": plan.video_size,
                "chunk_size": plan.chunk_size,
                "total_chunk_count": plan.total_chunk_count,
            },
        }
        initialized = self._post_json("/v2/post/publish/video/init/", init_payload, token)
        data = initialized.get("data") or {}
        publish_id = str(data.get("publish_id") or "")
        upload_url = str(data.get("upload_url") or "")
        if not publish_id or not upload_url:
            raise TikTokAPIError("TikTok không trả về phiên upload video hợp lệ")

        self._upload_file(video_path, upload_url, plan)
        try:
            status = self.fetch_status(publish_id)
        except TikTokPublisherError as exc:
            # The upload is already accepted. Do not report the whole publish as
            # failed because retrying here could create a duplicate TikTok post.
            status = {"status": "SUBMITTED", "status_check_error": str(exc)}
        return {
            "publish_id": publish_id,
            "title": title,
            "privacy_level": privacy_level,
            "status": status.get("status") or "SUBMITTED",
            "status_payload": status,
        }

    def fetch_status(self, publish_id: str) -> dict[str, Any]:
        token = self._valid_access_token()
        payload = self._post_json(
            "/v2/post/publish/status/fetch/",
            {"publish_id": publish_id},
            token,
        )
        return dict(payload.get("data") or {})

    def _upload_file(self, path: Path, upload_url: str, plan: UploadPlan) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
        with path.open("rb") as source:
            offset = 0
            for index in range(plan.total_chunk_count):
                remaining = plan.video_size - offset
                length = plan.chunk_size if index < plan.total_chunk_count - 1 else remaining
                chunk = source.read(length)
                if len(chunk) != length:
                    raise TikTokPublisherError("Không đọc đủ dữ liệu video để upload TikTok")
                end = offset + length - 1
                status, body = self._requester(
                    "PUT",
                    upload_url,
                    {
                        "Content-Type": content_type,
                        "Content-Length": str(length),
                        "Content-Range": f"bytes {offset}-{end}/{plan.video_size}",
                    },
                    chunk,
                )
                expected = 201 if index == plan.total_chunk_count - 1 else 206
                if status != expected:
                    detail = body.decode("utf-8", errors="replace")[:500]
                    raise TikTokAPIError(
                        f"TikTok từ chối chunk upload ({status}, cần {expected}): {detail}"
                    )
                offset = end + 1

    def _valid_access_token(self) -> str:
        self._require_configured()
        with self._lock:
            token = self._load_token()
            if not token:
                raise TikTokConfigurationError("Chưa kết nối tài khoản TikTok")
            now = self._clock()
            if float(token.get("expires_at", 0)) <= now + 600:
                refresh_token = str(token.get("refresh_token") or "")
                if not refresh_token or float(token.get("refresh_expires_at", 0)) <= now:
                    self.disconnect()
                    raise TikTokConfigurationError(
                        "Phiên TikTok đã hết hạn. Hãy kết nối tài khoản lại."
                    )
                refreshed = self._post_form(
                    "/v2/oauth/token/",
                    {
                        "client_key": self.config.client_key,
                        "client_secret": self.config.client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                )
                token = self._normalize_token(refreshed)
                self._save_token(token)
            return str(token["access_token"])

    def _normalize_token(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = self._clock()
        if not payload.get("access_token") or not payload.get("refresh_token"):
            raise TikTokAPIError("TikTok không trả về access token hợp lệ")
        normalized = dict(payload)
        normalized["expires_at"] = now + float(payload.get("expires_in", 0))
        normalized["refresh_expires_at"] = now + float(payload.get("refresh_expires_in", 0))
        return normalized

    def _post_form(self, path: str, values: dict[str, Any]) -> dict[str, Any]:
        status, body = self._requester(
            "POST",
            f"{self.config.api_base}{path}",
            {"Content-Type": "application/x-www-form-urlencoded"},
            urllib.parse.urlencode(values).encode("utf-8"),
        )
        payload = self._decode_response(status, body)
        if payload.get("error"):
            message = payload.get("error_description") or payload.get("error")
            raise TikTokAPIError(f"TikTok OAuth: {message}")
        return payload

    def _post_json(self, path: str, values: dict[str, Any], token: str) -> dict[str, Any]:
        status, body = self._requester(
            "POST",
            f"{self.config.api_base}{path}",
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json.dumps(values, ensure_ascii=False).encode("utf-8"),
        )
        payload = self._decode_response(status, body)
        error = payload.get("error") or {}
        code = error.get("code")
        if code and code != "ok":
            message = error.get("message") or code
            raise TikTokAPIError(f"TikTok API: {message}")
        return payload

    @staticmethod
    def _decode_response(status: int, body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TikTokAPIError(f"TikTok trả về dữ liệu không hợp lệ (HTTP {status})") from exc
        if not isinstance(payload, dict):
            raise TikTokAPIError(f"TikTok trả về dữ liệu không hợp lệ (HTTP {status})")
        if status < 200 or status >= 300:
            error = payload.get("error") or {}
            message = (
                payload.get("error_description")
                or error.get("message")
                or payload.get("message")
                or f"HTTP {status}"
            )
            raise TikTokAPIError(f"TikTok API: {message}")
        return payload

    def _require_configured(self) -> None:
        if not self.config.configured:
            raise TikTokConfigurationError(
                "Chưa cấu hình VIDTRANS_TIKTOK_CLIENT_KEY, "
                "VIDTRANS_TIKTOK_CLIENT_SECRET và VIDTRANS_TIKTOK_REDIRECT_URI"
            )

    def _load_token(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.token_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _save_token(self, payload: dict[str, Any]) -> None:
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.token_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(self.token_path)
