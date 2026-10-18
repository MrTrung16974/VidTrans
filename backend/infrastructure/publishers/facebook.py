from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from domain.social_publishing import PublishResult, PublishStatus, PublisherCapabilities, SocialPlatform
from infrastructure.publishers.oauth_store import OAuthAccountStore


class FacebookPublisherError(RuntimeError): pass
class FacebookConfigurationError(FacebookPublisherError): pass
class FacebookAPIError(FacebookPublisherError): pass


@dataclass(frozen=True)
class FacebookPublisherConfig:
    client_id: str = ""; client_secret: str = ""; redirect_uri: str = ""
    api_base: str = "https://graph.facebook.com/v24.0"
    authorize_url: str = "https://www.facebook.com/v24.0/dialog/oauth"
    scopes: tuple[str, ...] = ("pages_show_list", "pages_read_engagement", "pages_manage_posts")
    @classmethod
    def from_env(cls):
        return cls(os.environ.get("VIDTRANS_FACEBOOK_CLIENT_ID", "").strip(),
                   os.environ.get("VIDTRANS_FACEBOOK_CLIENT_SECRET", "").strip(),
                   os.environ.get("VIDTRANS_FACEBOOK_REDIRECT_URI", "").strip(),
                   os.environ.get("VIDTRANS_FACEBOOK_API_BASE", "https://graph.facebook.com/v24.0").rstrip("/"))
    @property
    def configured(self): return bool(self.client_id and self.client_secret and self.redirect_uri)


RequestFunction = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]
def _request(method, url, headers, body):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method=method), timeout=180) as response:
            return response.status, response.read()
    except Exception as exc:
        if hasattr(exc, "code"): return exc.code, exc.read()
        raise FacebookAPIError(f"Không kết nối được Facebook: {exc}") from exc


class FacebookPublisher:
    platform = SocialPlatform.FACEBOOK
    capabilities = PublisherCapabilities(caption_limit=5000, supports_remote_status=True)
    def __init__(self, auth_dir: Path, *, config=None, requester=None, clock=time.time, account_store=None):
        self.config = config or FacebookPublisherConfig.from_env(); self.store = account_store or OAuthAccountStore(auth_dir, clock=clock)
        self._requester = requester or _request; self._clock = clock
    def authorization_url(self):
        self._require(); return f"{self.config.authorize_url}?{urllib.parse.urlencode({'client_id':self.config.client_id,'redirect_uri':self.config.redirect_uri,'scope':','.join(self.config.scopes),'response_type':'code','state':self.store.new_state()})}"
    def exchange_code(self, code, state):
        self._require()
        if not self.store.consume_state(state): raise FacebookAPIError("Phiên kết nối Facebook không hợp lệ hoặc đã hết hạn")
        token = self._get("/oauth/access_token", {"client_id":self.config.client_id,"client_secret":self.config.client_secret,"redirect_uri":self.config.redirect_uri,"code":code})
        user_token = str(token.get("access_token") or "")
        pages = self._get("/me/accounts", {"fields":"id,name,access_token","access_token":user_token}).get("data") or []
        for page in pages:
            if page.get("id") and page.get("access_token"):
                self.store.save(str(page["id"]), {"account_id":str(page["id"]),"name":str(page.get("name") or ""),"access_token":str(page["access_token"])})
        if not pages: raise FacebookAPIError("Không tìm thấy Facebook Page có quyền đăng")
        return self.connection_status(str(pages[0]["id"]))
    def accounts(self): return [{"connection_id":x.get("connection_id"),"account_id":x.get("account_id"),"name":x.get("name"),"connected":True} for x in self.store.all()]
    def connection_status(self, account_id=None):
        token = self.store.load(account_id) if account_id else next(iter(self.store.all()), None)
        return {"platform":"FACEBOOK","configured":self.config.configured,"connected":bool(token),"connection_id":token.get("connection_id") if token else None,"account_id":token.get("account_id") if token else account_id,"name":token.get("name") if token else None}
    def disconnect(self, account_id): self.store.delete(account_id)
    def publish(self, request, *, on_checkpoint, on_progress=None):
        token = self._token(request.account_id); page_id = token["account_id"]
        started = self._post(f"/{page_id}/video_reels", {"upload_phase":"start","access_token":token["access_token"]})
        video_id, upload_url = str(started.get("video_id") or ""), str(started.get("upload_url") or "")
        if not video_id or not upload_url: raise FacebookAPIError("Facebook không trả về thông tin upload Reel")
        on_checkpoint(video_id, None)
        raw = request.video_path.read_bytes(); status, body = self._requester("POST", upload_url, {"Authorization":f"OAuth {token['access_token']}","Content-Type":"application/octet-stream","file_size":str(len(raw))}, raw)
        if not 200 <= status < 300: raise FacebookAPIError(f"Facebook upload Reel thất bại (HTTP {status})")
        if on_progress: on_progress(len(raw), len(raw))
        self._post(f"/{page_id}/video_reels", {"upload_phase":"finish","video_id":video_id,"video_state":"PUBLISHED","description":request.caption,"access_token":token["access_token"]})
        return PublishResult(PublishStatus.PROCESSING_REMOTE, video_id, video_id)
    def reconcile(self, *, remote_media_id, remote_post_id, account_id):
        token=self._token(account_id); video_id=remote_post_id or remote_media_id
        data=self._get(f"/{video_id}", {"fields":"status","access_token":token["access_token"]}); state=str((data.get("status") or {}).get("video_status") or "").lower()
        status=PublishStatus.PUBLISHED if state in {"ready","published"} else PublishStatus.FAILED if state in {"error","failed"} else PublishStatus.PROCESSING_REMOTE
        return PublishResult(status, remote_media_id, remote_post_id, data)
    def _token(self, account_id):
        if not account_id or not (token:=self.store.load(account_id)): raise FacebookConfigurationError("Hãy kết nối và chọn Facebook Page")
        return token
    def _require(self):
        if not self.config.configured: raise FacebookConfigurationError("Chưa cấu hình Facebook Developer App")
    def _get(self, path, values): return self._json("GET", f"{self.config.api_base}{path}?{urllib.parse.urlencode(values)}", None)
    def _post(self, path, values): return self._json("POST", f"{self.config.api_base}{path}", urllib.parse.urlencode(values).encode())
    def _json(self, method, url, body):
        status, raw=self._requester(method,url,{"Content-Type":"application/x-www-form-urlencoded"} if body else {},body)
        try: data=json.loads(raw.decode()) if raw else {}
        except Exception as exc: raise FacebookAPIError(f"Facebook trả về dữ liệu không hợp lệ (HTTP {status})") from exc
        if not 200 <= status < 300 or not isinstance(data,dict) or data.get("error"): raise FacebookAPIError(f"Facebook API: {((data.get('error') or {}).get('message') if isinstance(data,dict) else None) or f'HTTP {status}'}")
        return data
