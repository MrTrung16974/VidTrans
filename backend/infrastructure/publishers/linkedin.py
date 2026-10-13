from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from domain.social_publishing import PublishResult, PublishStatus, PublisherCapabilities, SocialPlatform


class LinkedInPublisherError(RuntimeError):
    pass


class LinkedInConfigurationError(LinkedInPublisherError):
    pass


class LinkedInAPIError(LinkedInPublisherError):
    pass


@dataclass(frozen=True)
class LinkedInPublisherConfig:
    access_token: str = ""
    owner_urn: str = ""
    api_base: str = "https://api.linkedin.com/rest"
    api_version: str = "202609"
    poll_attempts: int = 30
    poll_interval_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "LinkedInPublisherConfig":
        return cls(
            access_token=os.environ.get("VIDTRANS_LINKEDIN_ACCESS_TOKEN", "").strip(),
            owner_urn=os.environ.get("VIDTRANS_LINKEDIN_OWNER_URN", "").strip(),
            api_base=os.environ.get("VIDTRANS_LINKEDIN_API_BASE", "https://api.linkedin.com/rest").rstrip("/"),
            api_version=os.environ.get("VIDTRANS_LINKEDIN_API_VERSION", "202609").strip(),
        )

    @property
    def configured(self) -> bool:
        return bool(self.access_token and self.owner_urn and self.api_version)


RequestResult = tuple[int, bytes] | tuple[int, bytes, dict[str, str]]
RequestFunction = Callable[[str, str, dict[str, str], bytes | None], RequestResult]


def _default_request(method: str, url: str, headers: dict[str, str], body: bytes | None):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=900 if body else 90) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())
    except urllib.error.URLError as exc:
        raise LinkedInAPIError(f"Không kết nối được LinkedIn: {exc.reason}") from exc


class LinkedInPublisher:
    platform = SocialPlatform.LINKEDIN
    capabilities = PublisherCapabilities(
        supports_video=True, supports_text=False, supports_draft=False,
        supports_remote_status=True, supports_thumbnail=False,
        caption_limit=3000, schedule_horizon_days=365,
    )

    def __init__(self, *, config: LinkedInPublisherConfig | None = None,
                 requester: RequestFunction | None = None,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.config = config or LinkedInPublisherConfig.from_env()
        self._requester = requester or _default_request
        self._sleeper = sleeper

    def publish(self, request, *, on_checkpoint, on_progress=None):
        self._require_configured()
        owner = str(request.account_id or self.config.owner_urn).strip()
        size = request.video_path.stat().st_size
        initialized, _ = self._api_json("POST", "/videos?action=initializeUpload", {
            "initializeUploadRequest": {
                "owner": owner, "fileSizeBytes": size,
                "uploadCaptions": False, "uploadThumbnail": False,
            }
        })
        value = initialized.get("value") or {}
        video_urn = str(value.get("video") or "")
        instructions = value.get("uploadInstructions") or []
        upload_token = str(value.get("uploadToken") or "")
        if not video_urn or not isinstance(instructions, list) or not instructions:
            raise LinkedInAPIError("LinkedIn không trả về phiên upload hợp lệ")
        on_checkpoint(video_urn, None)
        data = request.video_path.read_bytes()
        part_ids = []
        uploaded = 0
        for instruction in instructions:
            first = int(instruction.get("firstByte", 0))
            last = int(instruction.get("lastByte", len(data) - 1))
            url = str(instruction.get("uploadUrl") or "")
            if not url or first < 0 or last < first or last >= len(data):
                raise LinkedInAPIError("LinkedIn trả về chỉ dẫn upload không hợp lệ")
            status, _raw, headers = self._request(
                "PUT", url, {"Content-Type": "application/octet-stream"}, data[first:last + 1]
            )
            if not 200 <= status < 300:
                raise LinkedInAPIError(f"LinkedIn từ chối upload video (HTTP {status})")
            part_id = self._header(headers, "etag")
            if not part_id:
                raise LinkedInAPIError("LinkedIn không trả về ETag của phần video")
            part_ids.append(part_id.strip('"'))
            uploaded += last - first + 1
            if on_progress:
                on_progress(uploaded, size)
        self._api_json("POST", "/videos?action=finalizeUpload", {
            "finalizeUploadRequest": {
                "video": video_urn, "uploadToken": upload_token,
                "uploadedPartIds": part_ids,
            }
        })
        detail = self._wait_ready(video_urn)
        title = str(request.options.get("title") or request.caption.splitlines()[0] or "Video")[:200]
        _payload, headers = self._api_json("POST", "/posts", {
            "author": owner,
            "commentary": request.caption,
            "visibility": str(request.options.get("visibility") or "PUBLIC"),
            "distribution": {
                "feedDistribution": "MAIN_FEED", "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "content": {"media": {"title": title, "id": video_urn}},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": bool(request.options.get("disable_reshare", False)),
        }, expected=(201,))
        post_id = self._header(headers, "x-restli-id")
        if not post_id:
            raise LinkedInAPIError("LinkedIn không trả về Post ID")
        on_checkpoint(video_urn, post_id)
        return PublishResult(status=PublishStatus.PUBLISHED, remote_media_id=video_urn,
                             remote_post_id=post_id, status_detail=detail)

    def reconcile(self, *, remote_media_id, remote_post_id, account_id):
        self._require_configured()
        if remote_post_id:
            return PublishResult(status=PublishStatus.PUBLISHED,
                                 remote_media_id=remote_media_id, remote_post_id=remote_post_id)
        if not remote_media_id:
            raise LinkedInAPIError("Thiếu LinkedIn video URN")
        detail, _ = self._api_json("GET", f"/videos/{urllib.parse.quote(str(remote_media_id), safe='')}")
        status = self._status(detail)
        if status == "AVAILABLE":
            return PublishResult(status=PublishStatus.NEEDS_RECONCILIATION,
                                 remote_media_id=remote_media_id, status_detail=detail)
        if status in {"PROCESSING_FAILED", "UPLOAD_FAILED", "ERROR"}:
            return PublishResult(status=PublishStatus.FAILED,
                                 remote_media_id=remote_media_id, status_detail=detail)
        return PublishResult(status=PublishStatus.PROCESSING_REMOTE,
                             remote_media_id=remote_media_id, status_detail=detail)

    def _wait_ready(self, video_urn: str):
        detail = {}
        for index in range(self.config.poll_attempts):
            detail, _ = self._api_json("GET", f"/videos/{urllib.parse.quote(video_urn, safe='')}")
            status = self._status(detail)
            if status == "AVAILABLE":
                return detail
            if status in {"PROCESSING_FAILED", "UPLOAD_FAILED", "ERROR"}:
                raise LinkedInAPIError("LinkedIn xử lý video thất bại")
            if index + 1 < self.config.poll_attempts:
                self._sleeper(self.config.poll_interval_seconds)
        raise LinkedInAPIError("LinkedIn chưa xử lý xong video; sẽ tiếp tục đối soát")

    @staticmethod
    def _status(payload: dict[str, Any]) -> str:
        value = payload.get("value")
        nested = value if isinstance(value, dict) else {}
        return str(payload.get("status") or nested.get("status") or "").upper()

    def _api_json(self, method: str, path: str, values: dict[str, Any] | None = None,
                  expected: tuple[int, ...] | None = None):
        body = json.dumps(values, ensure_ascii=False).encode() if values is not None else None
        status, raw, response_headers = self._request(method, f"{self.config.api_base}{path}", {
            "Authorization": f"Bearer {self.config.access_token}",
            "Linkedin-Version": self.config.api_version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }, body)
        try:
            payload = json.loads(raw.decode()) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LinkedInAPIError(f"LinkedIn trả về dữ liệu không hợp lệ (HTTP {status})") from exc
        valid = status in expected if expected else 200 <= status < 300
        if not isinstance(payload, dict) or not valid:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise LinkedInAPIError(f"LinkedIn API: {message or f'HTTP {status}'}")
        return payload, response_headers

    def _request(self, method: str, url: str, headers: dict[str, str], body: bytes | None):
        result = self._requester(method, url, headers, body)
        if len(result) == 2:
            status, raw = result
            return status, raw, {}
        return result

    @staticmethod
    def _header(headers: dict[str, str], name: str) -> str:
        wanted = name.lower()
        return next((str(value) for key, value in headers.items() if key.lower() == wanted), "")

    def _require_configured(self):
        if not self.config.configured:
            raise LinkedInConfigurationError(
                "Chưa cấu hình VIDTRANS_LINKEDIN_ACCESS_TOKEN và VIDTRANS_LINKEDIN_OWNER_URN"
            )
