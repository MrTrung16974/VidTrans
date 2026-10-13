from __future__ import annotations

import json
import mimetypes
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from domain.social_publishing import PublishResult, PublishStatus, PublisherCapabilities, SocialPlatform


class PinterestPublisherError(RuntimeError):
    pass


class PinterestConfigurationError(PinterestPublisherError):
    pass


class PinterestAPIError(PinterestPublisherError):
    pass


@dataclass(frozen=True)
class PinterestPublisherConfig:
    access_token: str = ""
    default_board_id: str = ""
    api_base: str = "https://api.pinterest.com/v5"
    poll_attempts: int = 30
    poll_interval_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "PinterestPublisherConfig":
        return cls(
            access_token=os.environ.get("VIDTRANS_PINTEREST_ACCESS_TOKEN", "").strip(),
            default_board_id=os.environ.get("VIDTRANS_PINTEREST_BOARD_ID", "").strip(),
            api_base=os.environ.get("VIDTRANS_PINTEREST_API_BASE", "https://api.pinterest.com/v5").rstrip("/"),
        )

    @property
    def configured(self) -> bool:
        return bool(self.access_token and self.default_board_id)


RequestFunction = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


def _default_request(method: str, url: str, headers: dict[str, str], body: bytes | None):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=900 if body else 90) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise PinterestAPIError(f"Không kết nối được Pinterest: {exc.reason}") from exc


class PinterestPublisher:
    platform = SocialPlatform.PINTEREST
    capabilities = PublisherCapabilities(
        supports_video=True, supports_text=False, supports_draft=False,
        supports_remote_status=True, supports_thumbnail=True,
        caption_limit=500, schedule_horizon_days=365,
    )

    def __init__(self, *, config: PinterestPublisherConfig | None = None,
                 requester: RequestFunction | None = None,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.config = config or PinterestPublisherConfig.from_env()
        self._requester = requester or _default_request
        self._sleeper = sleeper

    def publish(self, request, *, on_checkpoint, on_progress=None):
        self._require_configured()
        options = dict(request.options)
        board_id = str(options.get("board_id") or self.config.default_board_id).strip()
        cover_image_url = str(options.get("cover_image_url") or "").strip()
        if not board_id:
            raise PinterestConfigurationError("Hãy chọn Pinterest board")
        if not cover_image_url.startswith("https://"):
            raise PinterestConfigurationError("Pinterest video Pin cần cover_image_url HTTPS")
        registered = self._api_json("POST", "/media", {"media_type": "video"})
        media_id = str(registered.get("media_id") or "")
        upload_url = str(registered.get("upload_url") or "")
        parameters = registered.get("upload_parameters") or {}
        if not media_id or not upload_url or not isinstance(parameters, dict):
            raise PinterestAPIError("Pinterest không trả về phiên upload hợp lệ")
        on_checkpoint(media_id, None)
        self._upload(request.video_path, upload_url, parameters)
        if on_progress:
            size = request.video_path.stat().st_size
            on_progress(size, size)
        detail = self._wait_ready(media_id)
        title = str(options.get("title") or request.caption.splitlines()[0] or "Video")[:100]
        created = self._api_json("POST", "/pins", {
            "board_id": board_id,
            "title": title,
            "description": request.caption,
            "link": str(options.get("link") or "") or None,
            "media_source": {
                "source_type": "video_id", "media_id": media_id,
                "cover_image_url": cover_image_url,
            },
        })
        post_id = str(created.get("id") or "")
        if not post_id:
            raise PinterestAPIError("Pinterest không trả về Pin ID")
        on_checkpoint(media_id, post_id)
        return PublishResult(status=PublishStatus.PUBLISHED, remote_media_id=media_id,
                             remote_post_id=post_id, status_detail=detail)

    def reconcile(self, *, remote_media_id, remote_post_id, account_id):
        self._require_configured()
        if remote_post_id:
            return PublishResult(status=PublishStatus.PUBLISHED,
                                 remote_media_id=remote_media_id, remote_post_id=remote_post_id)
        if not remote_media_id:
            raise PinterestAPIError("Thiếu Pinterest media ID")
        detail = self._api_json("GET", f"/media/{urllib.parse.quote(str(remote_media_id), safe='')}")
        status = str(detail.get("status") or "").lower()
        if status == "succeeded":
            # Creating the Pin requires immutable options from the original request;
            # the application service must retry submit from its checkpoint path.
            return PublishResult(status=PublishStatus.NEEDS_RECONCILIATION,
                                 remote_media_id=remote_media_id, status_detail=detail)
        if status in {"failed", "expired"}:
            return PublishResult(status=PublishStatus.FAILED, remote_media_id=remote_media_id,
                                 status_detail=detail)
        return PublishResult(status=PublishStatus.PROCESSING_REMOTE,
                             remote_media_id=remote_media_id, status_detail=detail)

    def _wait_ready(self, media_id: str):
        detail = {}
        for index in range(self.config.poll_attempts):
            detail = self._api_json("GET", f"/media/{urllib.parse.quote(media_id, safe='')}")
            status = str(detail.get("status") or "").lower()
            if status == "succeeded":
                return detail
            if status in {"failed", "expired"}:
                raise PinterestAPIError("Pinterest xử lý video thất bại")
            if index + 1 < self.config.poll_attempts:
                self._sleeper(self.config.poll_interval_seconds)
        raise PinterestAPIError("Pinterest chưa xử lý xong video; sẽ tiếp tục đối soát")

    def _upload(self, path: Path, url: str, parameters: dict[str, Any]):
        boundary = f"----VidTrans{secrets.token_hex(16)}"
        parts = []
        for key, value in parameters.items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
        content_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\nContent-Type: {content_type}\r\n\r\n".encode()
            + path.read_bytes() + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        status, raw = self._requester("POST", url, {
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        }, b"".join(parts))
        if status != 204:
            raise PinterestAPIError(f"Pinterest từ chối media upload (HTTP {status})")

    def _api_json(self, method: str, path: str, values: dict[str, Any] | None = None):
        body = json.dumps(values, ensure_ascii=False).encode() if values is not None else None
        status, raw = self._requester(method, f"{self.config.api_base}{path}", {
            "Authorization": f"Bearer {self.config.access_token}",
            "Content-Type": "application/json",
        }, body)
        try:
            payload = json.loads(raw.decode()) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PinterestAPIError(f"Pinterest trả về dữ liệu không hợp lệ (HTTP {status})") from exc
        if not isinstance(payload, dict) or not 200 <= status < 300:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise PinterestAPIError(f"Pinterest API: {message or f'HTTP {status}'}")
        return payload

    def _require_configured(self):
        if not self.config.configured:
            raise PinterestConfigurationError(
                "Chưa cấu hình VIDTRANS_PINTEREST_ACCESS_TOKEN và VIDTRANS_PINTEREST_BOARD_ID"
            )

