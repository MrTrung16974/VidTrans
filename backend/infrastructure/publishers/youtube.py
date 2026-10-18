from __future__ import annotations

import json, os, time, urllib.parse, urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from domain.social_publishing import PublishResult, PublishStatus, PublisherCapabilities, SocialPlatform
from infrastructure.publishers.oauth_store import OAuthAccountStore

class YouTubePublisherError(RuntimeError): pass
class YouTubeConfigurationError(YouTubePublisherError): pass
class YouTubeAPIError(YouTubePublisherError): pass

@dataclass(frozen=True)
class YouTubePublisherConfig:
    client_id:str=""; client_secret:str=""; redirect_uri:str=""
    authorize_url:str="https://accounts.google.com/o/oauth2/v2/auth"; token_url:str="https://oauth2.googleapis.com/token"
    api_base:str="https://www.googleapis.com/youtube/v3"; upload_base:str="https://www.googleapis.com/upload/youtube/v3"
    @classmethod
    def from_env(cls): return cls(os.environ.get("VIDTRANS_YOUTUBE_CLIENT_ID","").strip(),os.environ.get("VIDTRANS_YOUTUBE_CLIENT_SECRET","").strip(),os.environ.get("VIDTRANS_YOUTUBE_REDIRECT_URI","").strip())
    @property
    def configured(self): return bool(self.client_id and self.client_secret and self.redirect_uri)

RequestFunction=Callable[[str,str,dict[str,str],bytes|None],tuple[int,bytes]]
def _request(method,url,headers,body):
    try:
        with urllib.request.urlopen(urllib.request.Request(url,data=body,headers=headers,method=method),timeout=300) as response: return response.status,response.read()
    except Exception as exc:
        if hasattr(exc,"code"): return exc.code,exc.read()
        raise YouTubeAPIError(f"Không kết nối được YouTube: {exc}") from exc

class YouTubePublisher:
    platform=SocialPlatform.YOUTUBE
    capabilities=PublisherCapabilities(caption_limit=5000,supports_remote_status=True,supports_thumbnail=True)
    def __init__(self,auth_dir:Path,*,config=None,requester=None,clock=time.time,account_store=None):
        self.config=config or YouTubePublisherConfig.from_env(); self.store=account_store or OAuthAccountStore(auth_dir,clock=clock); self._requester=requester or _request; self._clock=clock
    def authorization_url(self):
        self._require(); values={"client_id":self.config.client_id,"redirect_uri":self.config.redirect_uri,"response_type":"code","scope":"https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly","access_type":"offline","prompt":"consent","state":self.store.new_state()}; return f"{self.config.authorize_url}?{urllib.parse.urlencode(values)}"
    def exchange_code(self,code,state):
        self._require()
        if not self.store.consume_state(state): raise YouTubeAPIError("Phiên kết nối YouTube không hợp lệ hoặc đã hết hạn")
        token=self._form(self.config.token_url,{"client_id":self.config.client_id,"client_secret":self.config.client_secret,"redirect_uri":self.config.redirect_uri,"grant_type":"authorization_code","code":code})
        access=str(token.get("access_token") or ""); channel=self._get_json(f"{self.config.api_base}/channels?part=id,snippet&mine=true",access); items=channel.get("items") or []
        if not items: raise YouTubeAPIError("Không tìm thấy YouTube channel")
        item=items[0]; account_id=str(item.get("id") or ""); title=str((item.get("snippet") or {}).get("title") or "")
        previous = self.store.load(account_id) or {}
        refresh_token = str(token.get("refresh_token") or previous.get("refresh_token") or "")
        self.store.save(account_id,{"account_id":account_id,"name":title,"access_token":access,"refresh_token":refresh_token,"expires_at":self._clock()+float(token.get("expires_in") or 3600)})
        return self.connection_status(account_id)
    def accounts(self): return [{"connection_id":x.get("connection_id"),"account_id":x.get("account_id"),"name":x.get("name"),"connected":True} for x in self.store.all()]
    def connection_status(self,account_id=None):
        token=self.store.load(account_id) if account_id else next(iter(self.store.all()),None); return {"platform":"YOUTUBE","configured":self.config.configured,"connected":bool(token),"connection_id":token.get("connection_id") if token else None,"account_id":token.get("account_id") if token else account_id,"name":token.get("name") if token else None}
    def disconnect(self,account_id): self.store.delete(account_id)
    def publish(self,request,*,on_checkpoint,on_progress=None):
        token=self._token(request.account_id); access=self._access(token); options=dict(request.options); title=str(options.get("title") or "").strip()
        if not title: raise YouTubeConfigurationError("Tiêu đề YouTube là bắt buộc")
        privacy=str(options.get("privacy") or "private").lower()
        if privacy not in {"private","unlisted","public"}: raise YouTubeConfigurationError("Quyền riêng tư YouTube không hợp lệ")
        metadata={"snippet":{"title":title,"description":request.caption,"categoryId":str(options.get("category_id") or "22")},"status":{"privacyStatus":privacy}}
        video=request.video_path.read_bytes(); boundary="vidtrans-youtube-upload"; body=(f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}\r\n--{boundary}\r\nContent-Type: video/mp4\r\n\r\n".encode()+video+f"\r\n--{boundary}--\r\n".encode())
        url=f"{self.config.upload_base}/videos?uploadType=multipart&part=snippet,status"; status,raw=self._requester("POST",url,{"Authorization":f"Bearer {access}","Content-Type":f"multipart/related; boundary={boundary}","Content-Length":str(len(body))},body)
        try: result=json.loads(raw.decode()) if raw else {}
        except Exception as exc: raise YouTubeAPIError("YouTube trả về dữ liệu upload không hợp lệ") from exc
        video_id=str(result.get("id") or "")
        if not 200<=status<300 or not video_id: raise YouTubeAPIError(f"YouTube upload thất bại (HTTP {status})")
        on_checkpoint(video_id,video_id)
        if on_progress: on_progress(len(video),len(video))
        return PublishResult(PublishStatus.PROCESSING_REMOTE,video_id,video_id)
    def reconcile(self,*,remote_media_id,remote_post_id,account_id):
        token=self._token(account_id); video_id=remote_post_id or remote_media_id; data=self._get_json(f"{self.config.api_base}/videos?part=status,processingDetails&id={urllib.parse.quote(str(video_id or ''))}",self._access(token)); items=data.get("items") or []
        if not items: return PublishResult(PublishStatus.NEEDS_RECONCILIATION,remote_media_id,remote_post_id)
        detail=items[0]; processing=str((detail.get("processingDetails") or {}).get("processingStatus") or "").lower(); upload=str((detail.get("status") or {}).get("uploadStatus") or "").lower()
        status=PublishStatus.PUBLISHED if processing=="succeeded" or upload=="processed" else PublishStatus.FAILED if processing in {"failed","terminated"} or upload in {"failed","rejected"} else PublishStatus.PROCESSING_REMOTE
        return PublishResult(status,remote_media_id,remote_post_id,detail)
    def _token(self,account_id):
        if not account_id or not (token:=self.store.load(account_id)): raise YouTubeConfigurationError("Hãy kết nối và chọn YouTube channel")
        return token
    def _access(self,token):
        if float(token.get("expires_at",0))>self._clock()+300: return str(token["access_token"])
        refresh=str(token.get("refresh_token") or "")
        if not refresh: raise YouTubeConfigurationError("Phiên YouTube đã hết hạn. Hãy kết nối lại.")
        fresh=self._form(self.config.token_url,{"client_id":self.config.client_id,"client_secret":self.config.client_secret,"refresh_token":refresh,"grant_type":"refresh_token"}); access=str(fresh.get("access_token") or "")
        if not access: raise YouTubeAPIError("Google không trả về access token mới")
        token.update(access_token=access,expires_at=self._clock()+float(fresh.get("expires_in") or 3600)); self.store.save(str(token["account_id"]),token); return access
    def _require(self):
        if not self.config.configured: raise YouTubeConfigurationError("Chưa cấu hình Google OAuth App")
    def _form(self,url,values):
        status,raw=self._requester("POST",url,{"Content-Type":"application/x-www-form-urlencoded"},urllib.parse.urlencode(values).encode()); return self._decode(status,raw)
    def _get_json(self,url,access):
        status,raw=self._requester("GET",url,{"Authorization":f"Bearer {access}"},None); return self._decode(status,raw)
    @staticmethod
    def _decode(status,raw):
        try: data=json.loads(raw.decode()) if raw else {}
        except Exception as exc: raise YouTubeAPIError(f"YouTube trả về dữ liệu không hợp lệ (HTTP {status})") from exc
        if not 200<=status<300 or not isinstance(data,dict) or data.get("error"): raise YouTubeAPIError(f"YouTube API: HTTP {status}")
        return data
