from __future__ import annotations

import http.cookiejar
import importlib
import json
import math
import logging
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ContextManager
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

from infrastructure.tiktok_slideshow import MAX_IMAGES, render_slideshow, slideshow_duration

logger = logging.getLogger(__name__)


SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
SUPPORTED_SOCIAL_DOMAINS = {
    "docs.google.com",
    "douyin.com",
    "drive.google.com",
    "drive.usercontent.google.com",
    "facebook.com",
    "fb.com",
    "fb.watch",
    "iesdouyin.com",
    "tiktok.com",
    "xhslink.com",
    "xiaohongshu.com",
    "youtu.be",
    "youtube.com",
    "youtube-nocookie.com",
}
URL_PATTERN = re.compile(r"https?://[^\s<>\[\]\"']+", re.IGNORECASE)
TRAILING_URL_PUNCTUATION = ".,;:!?)]}，。；：！？）】》、"
YOUTUBE_AUTH_ERROR_MARKERS = (
    "sign in to confirm you’re not a bot",
    "sign in to confirm you're not a bot",
    "login required",
    "fresh cookies",
    "the page needs to be reloaded",
)
YOUTUBE_RELOAD_ERROR_MARKER = "the page needs to be reloaded"

# With account cookies, yt-dlp's default selection includes tv_downgraded.
# YouTube rejects that client with "The page needs to be reloaded" even though
# the browser session is valid, and that error can mask the clients that did
# work. Exclude it and keep the embedded web client as an extra fallback.
YOUTUBE_AUTHENTICATED_PLAYER_CLIENTS = ["default", "-tv_downgraded", "web_embedded"]


class SocialVideoDownloadError(RuntimeError):
    """A safe, user-facing failure while resolving or downloading a social video."""


class SocialVideoDownloadCancelled(SocialVideoDownloadError):
    pass


SLIDESHOW_KEY = "_vidtrans_slideshow"
SLIDESHOW_MAX_FILE_BYTES = 40 * 1024 * 1024
AUDIO_ONLY_EXTENSIONS = {"mp3", "m4a", "aac", "opus", "ogg", "wav", "flac"}


class _AudioOnlySource(RuntimeError):
    """yt-dlp resolved the post to audio only; the browser/API fallbacks may still find the video."""


def _has_no_video_track(info: dict[str, Any]) -> bool:
    """True when yt-dlp says every format is audio-only (vcodec "none").

    An unknown vcodec (missing key, as in the browser/API fallbacks) counts as video.
    """
    formats = info.get("formats") or [info]
    for item in formats:
        vcodec = item.get("vcodec")
        if vcodec != "none" and not (vcodec is None and str(item.get("ext") or "").lower() in AUDIO_ONLY_EXTENSIONS):
            return False
    return True


def _download_failure_message(platform: str, error: Exception, *, used_cookies: bool) -> str:
    message = " ".join(str(error).split())
    if platform == "YouTube" and any(marker in message.lower() for marker in YOUTUBE_AUTH_ERROR_MARKERS):
        if used_cookies:
            if YOUTUBE_RELOAD_ERROR_MARKER in message.lower():
                return (
                    "YouTube từ chối player tải tự động dù phiên trình duyệt vẫn có thể đang đăng nhập. "
                    "Hãy tải lại trang YouTube trong trình duyệt nguồn, phát thử video rồi chạy lại; "
                    "nếu vẫn lỗi, hãy cập nhật yt-dlp vì đây là thay đổi player của YouTube."
                )
            return (
                "YouTube vẫn yêu cầu xác minh dù đã gửi cookie đăng nhập. Nếu chỉ lỗi trên VPS, "
                "IP máy chủ có thể đã bị anti-bot đánh dấu; hãy cấu hình cùng một proxy residential "
                "cho VIDTRANS_SOCIAL_BROWSER_PROXY và VIDTRANS_YOUTUBE_PROXY, đăng nhập lại rồi thử lại."
            )
        return (
            "YouTube yêu cầu xác minh người dùng. Hãy chọn file cookies.txt ở Bước 1 "
            "hoặc cấu hình VIDTRANS_YTDLP_COOKIE_FILE trên máy chủ rồi thử lại."
        )
    return (
        f"Không tải được video {platform}: {message or 'nguồn từ chối truy cập'}. "
        "Nếu video yêu cầu đăng nhập, hãy cung cấp cookies.txt của chính tài khoản có quyền xem."
    )


def _xiaohongshu_failure_message(error: Exception, *, used_cookies: bool, note_type: str | None) -> str:
    message = " ".join(str(error).split()).lower()
    if not used_cookies:
        return (
            "Xiaohongshu chỉ cho xem bài khi đã đăng nhập. Vào trang Nguồn → tab Xiaohongshu, "
            "đăng nhập rồi bấm “Đã đăng nhập — Đồng bộ”, sau đó chạy lại."
        )
    if "no video formats" in message:
        if note_type == "normal":
            return "Link Xiaohongshu này là bài đăng ảnh (图文), không có video. Hãy dùng link một bài video."
        return (
            "Xiaohongshu không trả về video cho bài này: thường là bài đăng ảnh (图文), "
            "hoặc phiên đăng nhập cần xác minh lại. Mở Xiaohongshu ở trang Nguồn, kiểm tra rồi Đồng bộ."
        )
    return (
        "Không tải được video Xiaohongshu dù đã gửi cookie đăng nhập: phiên có thể đã hết hạn "
        "hoặc Xiaohongshu yêu cầu xác minh. Mở Xiaohongshu ở trang Nguồn, xác minh rồi bấm Đồng bộ."
    )


@dataclass(frozen=True)
class SocialVideoDownloadResult:
    path: Path
    source_url: str
    platform: str
    title: str
    video_id: str | None
    duration: float | None

    @property
    def display_filename(self) -> str:
        title = " ".join(self.title.split()).strip() or f"Video {self.platform}"
        title = "".join(character for character in title if character.isprintable())[:140].strip()
        return f"{title or f'Video {self.platform}'}{self.path.suffix.lower()}"


def _is_supported_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    return any(normalized == domain or normalized.endswith(f".{domain}") for domain in SUPPORTED_SOCIAL_DOMAINS)


def normalize_social_video_url(raw_url: str) -> str:
    candidate = raw_url.strip().rstrip(TRAILING_URL_PUNCTUATION)
    try:
        parsed = urlsplit(candidate)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Link mạng xã hội không hợp lệ") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise ValueError("Link phải bắt đầu bằng http:// hoặc https://")
    if parsed.username or parsed.password or port not in {None, 80, 443}:
        raise ValueError("Link mạng xã hội chứa thông tin kết nối không được hỗ trợ")
    if not _is_supported_host(hostname):
        raise ValueError(f"Chưa hỗ trợ tải video từ tên miền {hostname}")
    scheme = "https" if parsed.scheme.lower() == "https" else "http"
    netloc = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


SUPPORTED_LINK_FORMS_HINT = (
    "Hỗ trợ link một video: TikTok (/@user/video/…, /@user/photo/…, vm/vt.tiktok.com), "
    "Douyin (/video/…, modal_id=…, v.douyin.com), YouTube (watch?v=…, shorts, youtu.be, live, embed), "
    "Facebook (reel, videos, watch?v=…, share, posts, fb.watch), "
    "Xiaohongshu (xhslink.com, /explore/…, /discovery/item/…), "
    "Google Drive (drive.google.com/file/d/…, open?id=…, uc?id=…; file chia sẻ \"Bất kỳ ai có đường liên kết\")"
)
_SHORT_LINK_HOSTS = {"vm.tiktok.com", "vt.tiktok.com", "v.douyin.com", "fb.watch", "xhslink.com"}
XIAOHONGSHU_PLATFORM = "Xiaohongshu"
_XIAOHONGSHU_NOTE_PATH = re.compile(
    r"^/(?:explore|discovery/item|user/profile/[0-9a-zA-Z]+)/([0-9a-f]{24})/?$"
)
_XIAOHONGSHU_LOGIN_PATHS = ("/login", "/website-login")
# The note page refuses a request without the share token; tracking parameters are dropped.
_XIAOHONGSHU_KEPT_QUERY = ("xsec_token", "xsec_source")


def xiaohongshu_note_url(url: str, *, depth: int = 0) -> tuple[str | None, str | None]:
    """(canonical /explore/<id> URL, note type from the share link) for any note link form.

    Without a login, xhslink.com short links land on the login page; the note itself
    is then in its ``redirectPath`` parameter. The share link's ``type`` is ``normal``
    for an image post and ``video`` for a video.
    """
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query)
    if not (hostname == "xiaohongshu.com" or hostname.endswith(".xiaohongshu.com")):
        return None, None
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path.rstrip("/") in _XIAOHONGSHU_LOGIN_PATHS:
        target = (query.get("redirectPath") or query.get("redirect_path") or [""])[0]
        if not target or depth > 2:
            return None, None
        # parse_qs already decoded it once; only a doubly encoded target still hides its "?".
        if "?" not in target and "%3f" in target.lower():
            target = unquote(target)
        return xiaohongshu_note_url(target, depth=depth + 1)
    match = _XIAOHONGSHU_NOTE_PATH.match(path)
    if not match:
        return None, None
    kept = [(key, query[key][0]) for key in _XIAOHONGSHU_KEPT_QUERY if (query.get(key) or [""])[0]]
    suffix = f"?{urlencode(kept)}" if kept else ""
    note_type = (query.get("type") or [""])[0].strip().lower() or None
    return f"https://www.xiaohongshu.com/explore/{match.group(1)}{suffix}", note_type
_TIKTOK_VIDEO_PATH = re.compile(
    r"^/(?:@[^/]+/(?:video|photo)/\d+|t/[\w-]+|v/\d+(?:\.html)?|embed(?:/v2)?/\d+|player/v1/\d+|share/video/\d+)/?$"
)
_YOUTUBE_ID_PATH = re.compile(r"^/(?:shorts|embed|live|v)/[\w-]{3,}/?$")
_FACEBOOK_VIDEO_PATH = re.compile(
    r"^/(?:"
    r"reels?/[\w-]+"
    r"|watch/?|video\.php|story\.php|permalink\.php"
    r"|share/(?:v/|r/|p/)?[\w-]+"
    r"|(?:[^/]+/)?videos/(?:[^/]+/)?\d+"
    r"|[^/]+/posts/[\w-]+"
    r"|groups/[^/]+/(?:posts|permalink)/\d+"
    r")/?$"
)
_FACEBOOK_QUERY_KEYS = {
    "/watch": "v", "/watch/": "v", "/video.php": "v", "/story.php": "story_fbid", "/permalink.php": "story_fbid",
}


GOOGLE_DRIVE_PLATFORM = "Google Drive"
_DRIVE_ID = r"[\w-]{10,}"
_DRIVE_FILE_PATH = re.compile(rf"^/(?:a/[^/]+/)?file/(?:u/\d+/)?d/({_DRIVE_ID})(?:/[\w-]*)?/?$")
_DRIVE_QUERY_PATHS = {"/open", "/uc", "/download", "/u/0/uc", "/u/1/uc", "/u/0/open", "/u/1/open"}
_DRIVE_FOLDER_PATH = re.compile(r"/(?:drive/)?(?:u/\d+/)?folders/")
_GOOGLE_EDITOR_PATH = re.compile(r"^/(?:document|spreadsheets|presentation|forms|drawings)/")


def google_drive_file_id(url: str) -> str | None:
    """File id from any Drive share/open/download link form, or None."""
    parsed = urlsplit(url)
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    match = _DRIVE_FILE_PATH.match(path)
    if match:
        return match.group(1)
    if path.rstrip("/") in _DRIVE_QUERY_PATHS:
        value = (parse_qs(parsed.query).get("id") or [""])[0].strip()
        if re.fullmatch(_DRIVE_ID, value):
            return value
    return None


def canonical_google_drive_url(url: str) -> str:
    """One canonical form per file (dedupes batches), keeping the resourcekey old shares need."""
    file_id = google_drive_file_id(url)
    if file_id is None:
        return url
    resource_key = (parse_qs(urlsplit(url).query).get("resourcekey") or [""])[0].strip()
    suffix = f"?resourcekey={quote(resource_key, safe='')}" if resource_key else ""
    return f"https://drive.google.com/file/d/{file_id}/view{suffix}"


def _google_drive_issue(url: str) -> str | None:
    parsed = urlsplit(url)
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if google_drive_file_id(url):
        return None
    if _DRIVE_FOLDER_PATH.search(path):
        return "link thư mục Google Drive chưa được hỗ trợ, hãy dán link chia sẻ của từng file video"
    if _GOOGLE_EDITOR_PATH.match(path):
        return "đây là tài liệu Google Docs/Sheets/Slides, không phải file video"
    return "không tìm thấy mã file Google Drive trong link (cần /file/d/<id> hoặc open?id=<id>)"


def social_video_download_issue(url: str) -> str | None:
    """Why an allow-listed link has no download method, or None when the downloader can fetch it.

    Domain allow-listing alone accepts profile pages, home pages and playlists, which then
    fail minutes later in the queue. Only the single-post shapes each downloader resolves pass.
    """
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query = parse_qs(parsed.query)
    platform = social_platform(url)
    if platform == GOOGLE_DRIVE_PLATFORM:
        return _google_drive_issue(url)
    if hostname in _SHORT_LINK_HOSTS or hostname == "youtu.be":
        if path.strip("/"):
            return None
        return "link rút gọn thiếu mã video"
    if platform == "TikTok":
        if _TIKTOK_VIDEO_PATH.match(path):
            return None
        return "đây không phải link một video TikTok (trang cá nhân, trang chủ hoặc trang khác)"
    if platform == "Douyin":
        video_id, kind = douyin_video_id(url)
        if kind in {"note", "slides"}:
            return "đây là bài đăng ảnh (图文) Douyin, không phải video"
        if video_id:
            return None
        return "không tìm thấy mã video Douyin trong link (cần /video/<id> hoặc modal_id=<id>)"
    if platform == "YouTube":
        if path.rstrip("/") == "/watch" and (query.get("v") or [""])[0].strip():
            return None
        if _YOUTUBE_ID_PATH.match(path):
            return None
        if query.get("list"):
            return "link playlist YouTube chưa được hỗ trợ, hãy dán link từng video"
        return "đây không phải link một video YouTube (kênh, trang chủ hoặc trang tìm kiếm)"
    if platform == XIAOHONGSHU_PLATFORM:
        if xiaohongshu_note_url(url)[0]:
            return None
        return "đây không phải link một bài Xiaohongshu (cần xhslink.com/…, /explore/<id> hoặc /discovery/item/<id>)"
    if platform == "Facebook":
        if not _FACEBOOK_VIDEO_PATH.match(path):
            return "đây không phải link một video/bài đăng Facebook (trang cá nhân, fanpage hoặc trang chủ)"
        required_key = _FACEBOOK_QUERY_KEYS.get(path)
        if required_key and not (query.get(required_key) or [""])[0].strip():
            return f"link Facebook thiếu tham số {required_key}="
        return None
    return "nền tảng chưa có phương thức tải"


def ensure_downloadable_social_url(raw_url: str) -> str:
    """Normalize a link and reject it unless a download method exists for it."""
    url = normalize_social_video_url(raw_url)
    issue = social_video_download_issue(url)
    if issue:
        raise ValueError(f"Link chưa có phương thức tải: {url} — {issue}")
    if social_platform(url) == GOOGLE_DRIVE_PLATFORM:
        return canonical_google_drive_url(url)
    return url


def extract_social_video_urls(share_text: str, *, limit: int = 50) -> list[str]:
    """Extract unique downloadable social video links from URLs or copied share messages.

    Every link that cannot be downloaded is reported together, so the user fixes the
    batch in one pass instead of one rejection per submit.
    """

    candidates = URL_PATTERN.findall(share_text or "")
    if not candidates:
        return []
    urls: list[str] = []
    seen: set[str] = set()
    problems: list[str] = []
    for candidate in candidates:
        try:
            normalized = ensure_downloadable_social_url(candidate)
        except ValueError as exc:
            message = str(exc)
            if message not in problems:
                problems.append(message)
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
        if len(urls) > limit:
            raise ValueError(f"Chỉ được nhập tối đa {limit} link trong một batch")
    if problems:
        shown = problems[:5]
        more = f"; và {len(problems) - len(shown)} link khác" if len(problems) > len(shown) else ""
        raise ValueError(
            f"Có {len(problems)} link không thể tải: " + "; ".join(shown) + more
            + f". {SUPPORTED_LINK_FORMS_HINT}."
        )
    return urls


def social_platform(url: str) -> str:
    hostname = (urlsplit(url).hostname or "").lower()
    platform_domains = (
        ("Douyin", ("douyin.com", "iesdouyin.com")),
        ("TikTok", ("tiktok.com",)),
        ("YouTube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
        ("Facebook", ("facebook.com", "fb.com", "fb.watch")),
        (XIAOHONGSHU_PLATFORM, ("xiaohongshu.com", "xhslink.com")),
        (GOOGLE_DRIVE_PLATFORM, ("drive.google.com", "docs.google.com", "drive.usercontent.google.com")),
    )
    for platform, domains in platform_domains:
        if any(hostname == domain or hostname.endswith("." + domain) for domain in domains):
            return platform
    raise ValueError("Tên miền mạng xã hội chưa được hỗ trợ")


_DOUYIN_PATH_ID = re.compile(r"/(?:share/)?(video|note|slides)/(\d+)")


def douyin_video_id(url: str) -> tuple[str | None, str | None]:
    """Return (aweme id, kind) from any Douyin page/share URL form."""
    parsed = urlsplit(url)
    match = _DOUYIN_PATH_ID.search(parsed.path)
    if match:
        return match.group(2), match.group(1)
    query = parse_qs(parsed.query)
    for key in ("modal_id", "vid", "aweme_id"):
        value = (query.get(key) or [""])[0]
        if value.isdigit():
            return value, "video"
    return None, None


def _follow_redirects(url: str, *, timeout: int, proxy: str | None) -> str:
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    })
    with urllib.request.build_opener(handler).open(request, timeout=timeout) as response:
        return response.geturl()


# Google Drive serves the uploaded file itself, so any common container is accepted;
# ffprobe validates the stream afterwards.
DRIVE_VIDEO_EXTENSIONS = SUPPORTED_VIDEO_EXTENSIONS | {
    ".3gp", ".flv", ".m2ts", ".mpeg", ".mpg", ".mts", ".ts", ".wmv",
}
_DRIVE_DOWNLOAD_URL = "https://drive.usercontent.google.com/download"
_DRIVE_CHUNK_BYTES = 1024 * 1024
_DRIVE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


class _DriveConfirmForm(HTMLParser):
    """The "can't scan this file for viruses" page: a GET form whose inputs unlock the file."""

    def __init__(self) -> None:
        super().__init__()
        self.action: str | None = None
        self.fields: dict[str, str] = {}
        self._inside = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "form" and (values.get("id") == "download-form" or "download" in values.get("action", "")):
            self._inside = True
            self.action = values.get("action") or _DRIVE_DOWNLOAD_URL
        elif tag == "input" and self._inside and values.get("name"):
            self.fields[values["name"]] = values.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._inside = False


def _content_disposition_filename(header: str | None) -> str:
    if not header:
        return ""
    encoded = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", header, re.IGNORECASE)
    if encoded:
        return unquote(encoded.group(1).strip().strip('"'))
    plain = re.search(r'filename\s*=\s*"([^"]*)"|filename\s*=\s*([^;]+)', header, re.IGNORECASE)
    if plain:
        return (plain.group(1) or plain.group(2) or "").strip()
    return ""


def _drive_html_failure(html: str, final_url: str) -> str:
    lowered = html.lower()
    if "accounts.google.com" in final_url or "servicelogin" in lowered or "accounts.google.com" in lowered[:4000]:
        return (
            "File Google Drive chưa chia sẻ công khai. Hãy đặt quyền chia sẻ thành "
            "\"Bất kỳ ai có đường liên kết\" hoặc gửi cookies.txt của tài khoản có quyền xem"
        )
    if "quota" in lowered or "too many users" in lowered or "quá nhiều người" in lowered:
        return (
            "Google Drive tạm khóa lượt tải file này vì vượt hạn mức tải trong 24 giờ. "
            "Hãy tạo bản sao file trong Drive của bạn, chia sẻ bản sao rồi thử lại"
        )
    if "not found" in lowered or "không tìm thấy" in lowered or "does not exist" in lowered:
        return "File Google Drive không tồn tại hoặc đã bị xóa"
    return "Google Drive không trả về file video (file có thể bị giới hạn quyền tải xuống)"


ProgressCallback = Callable[[int, int | None], None]
CancelCallback = Callable[[], bool]
YoutubeDLFactory = Callable[[dict[str, Any]], ContextManager[Any]]


class SocialVideoDownloader:
    """Download one allow-listed social video through yt-dlp."""

    def __init__(
        self,
        *,
        ffmpeg_location: str | None = None,
        max_bytes: int = 6 * 1024 * 1024 * 1024,
        max_duration_seconds: int = 6 * 60 * 60,
        socket_timeout: int = 30,
        cookie_file: Path | None = None,
        ydl_factory: YoutubeDLFactory | None = None,
        douyin_cookie_provider: Callable[[Path], Path] | None = None,
        platform_cookie_providers: dict[str, Callable[[Path], Path]] | None = None,
        platform_user_agent_providers: dict[str, Callable[[], str]] | None = None,
        douyin_resolver: Callable[..., dict[str, Any]] | None = None,
        facebook_resolver: Callable[..., dict[str, Any]] | None = None,
        tiktok_proxy: str | None = None,
        douyin_proxy: str | None = None,
        youtube_proxy: str | None = None,
        redirect_resolver: Callable[[str], str] | None = None,
        ffprobe_location: str | None = None,
        http_opener_factory: Callable[[http.cookiejar.CookieJar], Any] | None = None,
    ) -> None:
        self.ffmpeg_location = ffmpeg_location
        self.ffprobe_location = ffprobe_location
        self._http_opener_factory = http_opener_factory or (
            lambda jar: urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        )
        self.max_bytes = max_bytes
        self.max_duration_seconds = max_duration_seconds
        self.socket_timeout = socket_timeout
        self.cookie_file = cookie_file
        self._ydl_factory = ydl_factory
        self._douyin_cookie_provider = douyin_cookie_provider
        self._platform_cookie_providers = dict(platform_cookie_providers or {})
        self._platform_user_agent_providers = dict(platform_user_agent_providers or {})
        self._douyin_resolver = douyin_resolver
        self._facebook_resolver = facebook_resolver
        self.tiktok_proxy = tiktok_proxy
        self.douyin_proxy = douyin_proxy
        self.youtube_proxy = youtube_proxy
        self._redirect_resolver = redirect_resolver or (
            lambda url: _follow_redirects(url, timeout=self.socket_timeout, proxy=self.douyin_proxy)
        )

    def canonical_xiaohongshu_url(self, url: str) -> tuple[str, str | None]:
        """Resolve xhslink.com short links and keep only what the note page needs."""
        note_url, note_type = xiaohongshu_note_url(url)
        if note_url:
            return note_url, note_type
        try:
            resolved = self._redirect_resolver(url)
        except Exception as exc:
            raise SocialVideoDownloadError(
                "Không mở được link rút gọn Xiaohongshu; hãy sao chép lại link chia sẻ (link có thể đã hết hạn)"
            ) from exc
        note_url, note_type = xiaohongshu_note_url(resolved)
        if not note_url:
            raise SocialVideoDownloadError(
                "Link Xiaohongshu không dẫn tới một bài đăng; hãy sao chép lại link chia sẻ của bài video"
            )
        return note_url, note_type

    def canonical_douyin_url(self, url: str) -> str:
        """Map share/modal/short links to the /video/<id> page yt-dlp's Douyin extractor supports.

        Other forms fall through to yt-dlp's generic extractor, which grabs whatever
        video appears first in the page HTML (often a recommended clip).
        """
        video_id, kind = douyin_video_id(url)
        if video_id is None:
            try:
                final_url = self._redirect_resolver(url)
            except Exception as exc:
                logger.warning("Cannot resolve Douyin short link: %s", type(exc).__name__)
                return url
            video_id, kind = douyin_video_id(final_url)
            if video_id is None:
                return url
        if kind in {"note", "slides"}:
            raise SocialVideoDownloadError(
                "Link Douyin này là bài đăng ảnh (图文), không phải video. Hãy chọn link một video."
            )
        return f"https://www.douyin.com/video/{video_id}"

    def _create_ydl(self, options: dict[str, Any]) -> ContextManager[Any]:
        if self._ydl_factory is not None:
            return self._ydl_factory(options)
        try:
            yt_dlp = importlib.import_module("yt_dlp")
        except ImportError as exc:  # pragma: no cover - exercised by deployment checks
            raise SocialVideoDownloadError(
                "Thiếu yt-dlp. Hãy build lại Docker image để cài bộ tải video mạng xã hội."
            ) from exc
        return yt_dlp.YoutubeDL(options)

    def _tikwm_info(self, url: str) -> dict[str, Any] | None:
        """yt-dlp info for the HD (falling back to SD) no-watermark file from tikwm.com."""
        request = urllib.request.Request(
            f"https://www.tikwm.com/api/?url={quote(url, safe='')}&hd=1",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=self.socket_timeout) as response:
            payload = json.loads(response.read(2 * 1024 * 1024))
        data = payload.get("data") or {}
        if payload.get("code") != 0:
            return None
        images = [str(image) for image in (data.get("images") or []) if image]
        if images:
            music = data.get("music") or (data.get("music_info") or {}).get("play")
            return {"id": str(data.get("id") or "photo"), "title": data.get("title") or "Ảnh TikTok",
                    "duration": slideshow_duration(min(len(images), MAX_IMAGES)), "ext": "mp4",
                    SLIDESHOW_KEY: {"images": images[:MAX_IMAGES], "music": str(music) if music else None}}
        play = data.get("hdplay") or data.get("play")
        if not play:
            return None
        return {"id": str(data.get("id") or "video"), "title": data.get("title") or "Video TikTok",
                "duration": data.get("duration"), "ext": "mp4",
                "url": urljoin("https://www.tikwm.com/", play)}

    def _download_slideshow(
        self,
        info: dict[str, Any],
        destination_stem: Path,
        *,
        progress_callback: ProgressCallback | None,
        cancel_requested: CancelCallback | None,
    ) -> dict[str, Any]:
        """Fetch a photo post's images and soundtrack, render them to ``<stem>.mp4``."""
        slideshow = info[SLIDESHOW_KEY]
        image_urls: list[str] = slideshow["images"]
        scratch = destination_stem.with_name(destination_stem.name + ".slideshow")
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True)
        try:
            images: list[Path] = []
            for index, image_url in enumerate(image_urls):
                if cancel_requested and cancel_requested():
                    raise SocialVideoDownloadCancelled("Đã hủy khi đang tải video nguồn")
                images.append(self._fetch_to(image_url, scratch / f"image_{index:02d}"))
                if progress_callback:
                    progress_callback(index + 1, len(image_urls) + 1)
            audio = None
            if slideshow.get("music"):
                try:
                    audio = self._fetch_to(slideshow["music"], scratch / "music")
                except Exception as music_error:
                    logger.warning("TikTok slideshow music download failed; using silence: %s", music_error)
            if cancel_requested and cancel_requested():
                raise SocialVideoDownloadCancelled("Đã hủy khi đang tải video nguồn")
            output = destination_stem.with_name(destination_stem.name + ".mp4")
            temporary = scratch / "slideshow.mp4"
            try:
                duration = render_slideshow(self.ffmpeg_location or "ffmpeg", images, audio, temporary)
            except subprocess.CalledProcessError as render_error:
                detail = (render_error.stderr or b"").decode("utf-8", errors="ignore")[-300:]
                logger.warning("TikTok slideshow render failed: %s", detail)
                raise SocialVideoDownloadError("Không dựng được video từ bài đăng ảnh TikTok") from render_error
            temporary.replace(output)
            if progress_callback:
                progress_callback(len(image_urls) + 1, len(image_urls) + 1)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        logger.info("Rendered TikTok photo post %s from %d images", info.get("id"), len(images))
        return {"id": info.get("id"), "title": info.get("title"), "duration": duration}

    def _fetch_to(self, url: str, stem: Path) -> Path:
        """Download ``url`` to ``stem`` (size-capped) and return the written path."""
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tiktok.com/"})
        with urllib.request.urlopen(request, timeout=self.socket_timeout) as response:
            data = response.read(SLIDESHOW_MAX_FILE_BYTES + 1)
        if len(data) > SLIDESHOW_MAX_FILE_BYTES:
            raise SocialVideoDownloadError("Ảnh trong bài đăng TikTok quá lớn")
        if not data:
            raise RuntimeError(f"Empty file from {urlsplit(url).netloc}")
        stem.write_bytes(data)
        return stem

    def _ssstik_info(self, url: str) -> dict[str, Any] | None:
        """yt-dlp info for the download link ssstik.io renders, or None when it has none."""
        try:
            request = urllib.request.Request(
                "https://ssstik.io/abc?url=dl",
                data=urlencode({"id": url, "locale": "en", "tt": "1"}).encode("utf-8"),
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Hx-Request": "true",
                },
            )
            with urllib.request.urlopen(request, timeout=self.socket_timeout) as response:
                html = response.read(10 * 1024 * 1024).decode("utf-8", errors="ignore")
        except Exception as ssstik_err:
            logger.warning("ssstik.io fallback failed: %s", ssstik_err)
            return None
        match = re.search(r'class="[^"]*download_link[^"]*".*?href="([^"#]+)"', html)
        if not match:
            match = re.search(r'class="[^"]*download_link[^"]*".*?data-directurl="([^"#]+)"', html)
        if not match:
            return None
        return {"id": "video", "title": "Video TikTok", "duration": None, "ext": "mp4",
                "url": match.group(1).replace("&amp;", "&"), "http_headers": {"Referer": "https://ssstik.io/"}}

    def _probe_duration(self, path: Path) -> float | None:
        ffprobe = self.ffprobe_location or shutil.which("ffprobe")
        if not ffprobe:
            return None
        try:
            completed = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                check=True, capture_output=True, text=True, timeout=60,
            )
            return float(completed.stdout.strip())
        except Exception:
            return None

    def _download_google_drive(
        self,
        url: str,
        destination_stem: Path,
        *,
        progress_callback: ProgressCallback | None,
        cancel_requested: CancelCallback | None,
        cookie_file: Path | None,
    ) -> SocialVideoDownloadResult:
        """Fetch the original uploaded file (not a re-encoded preview stream) from Drive."""
        file_id = google_drive_file_id(url)
        if file_id is None:
            raise SocialVideoDownloadError("Không tìm thấy mã file Google Drive trong link")
        jar: http.cookiejar.CookieJar = http.cookiejar.MozillaCookieJar()
        effective_cookie_file = cookie_file or self.cookie_file
        if effective_cookie_file and effective_cookie_file.is_file():
            try:
                jar.load(str(effective_cookie_file), ignore_discard=True, ignore_expires=True)  # type: ignore[attr-defined]
            except Exception as cookie_error:
                logger.warning("Cannot read cookies for Google Drive: %s", type(cookie_error).__name__)
        opener = self._http_opener_factory(jar)
        params = {"id": file_id, "export": "download", "confirm": "t"}
        resource_key = (parse_qs(urlsplit(url).query).get("resourcekey") or [""])[0].strip()
        if resource_key:
            params["resourcekey"] = resource_key
        request_url = f"{_DRIVE_DOWNLOAD_URL}?{urlencode(params)}"
        headers = {"User-Agent": _DRIVE_USER_AGENT, "Accept": "*/*"}

        def cancelled() -> bool:
            return bool(cancel_requested and cancel_requested())

        partial = destination_stem.with_name(destination_stem.name + ".drive.part")
        try:
            for attempt in range(2):
                if cancelled():
                    raise SocialVideoDownloadCancelled("Đã hủy khi đang tải video nguồn")
                request = urllib.request.Request(request_url, headers=headers)
                try:
                    response = opener.open(request, timeout=self.socket_timeout)
                except urllib.error.HTTPError as http_error:
                    if http_error.code in {401, 403}:
                        raise SocialVideoDownloadError(_drive_html_failure("servicelogin", "")) from http_error
                    if http_error.code == 404:
                        raise SocialVideoDownloadError("File Google Drive không tồn tại hoặc đã bị xóa") from http_error
                    if http_error.code == 429:
                        raise SocialVideoDownloadError(_drive_html_failure("quota", "")) from http_error
                    raise SocialVideoDownloadError(
                        f"Google Drive trả lỗi HTTP {http_error.code} khi tải file"
                    ) from http_error
                with response:
                    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    disposition = response.headers.get("Content-Disposition")
                    if content_type.startswith("text/html") and not disposition:
                        html = response.read(2 * 1024 * 1024).decode("utf-8", errors="ignore")
                        form = _DriveConfirmForm()
                        form.feed(html)
                        if attempt == 0 and form.fields.get("id"):
                            # Large files: Drive asks to confirm skipping the virus scan.
                            request_url = f"{urljoin(_DRIVE_DOWNLOAD_URL, form.action or '')}?{urlencode(form.fields)}"
                            continue
                        raise SocialVideoDownloadError(_drive_html_failure(html, response.geturl()))
                    filename = _content_disposition_filename(disposition)
                    extension = Path(filename).suffix.lower()
                    if extension not in DRIVE_VIDEO_EXTENSIONS:
                        if not content_type.startswith("video/"):
                            shown = filename or content_type or "không rõ loại"
                            raise SocialVideoDownloadError(
                                f"File Google Drive không phải video ({shown}). "
                                f"Hỗ trợ: {', '.join(sorted(ext.lstrip('.') for ext in DRIVE_VIDEO_EXTENSIONS))}"
                            )
                        extension = ".mp4"
                    total_header = response.headers.get("Content-Length")
                    total = int(total_header) if total_header and total_header.isdigit() else None
                    if total is not None and total > self.max_bytes:
                        raise SocialVideoDownloadError(
                            f"Video lớn quá giới hạn {self.max_bytes // (1024 * 1024)} MB"
                        )
                    downloaded = 0
                    with partial.open("wb") as output:
                        while True:
                            if cancelled():
                                raise SocialVideoDownloadCancelled("Đã hủy khi đang tải video nguồn")
                            chunk = response.read(_DRIVE_CHUNK_BYTES)
                            if not chunk:
                                break
                            downloaded += len(chunk)
                            if downloaded > self.max_bytes:
                                raise SocialVideoDownloadError(
                                    f"Video lớn quá giới hạn {self.max_bytes // (1024 * 1024)} MB"
                                )
                            output.write(chunk)
                            if progress_callback:
                                progress_callback(downloaded, total)
                    if downloaded == 0:
                        raise SocialVideoDownloadError("File video tải về bị rỗng")
                    if total is not None and downloaded < total:
                        raise SocialVideoDownloadError(
                            "Tải Google Drive bị ngắt giữa chừng, hãy thử lại"
                        )
                    break
            else:  # pragma: no cover - the loop always breaks or raises
                raise SocialVideoDownloadError("Google Drive không trả về file video")
            output_path = destination_stem.with_name(destination_stem.name + extension)
            partial.replace(output_path)
        except SocialVideoDownloadError:
            partial.unlink(missing_ok=True)
            raise
        except Exception as exc:
            partial.unlink(missing_ok=True)
            if cancelled():
                raise SocialVideoDownloadCancelled("Đã hủy khi đang tải video nguồn") from exc
            raise SocialVideoDownloadError(
                f"Không tải được file Google Drive: {' '.join(str(exc).split()) or type(exc).__name__}"
            ) from exc

        duration = self._probe_duration(output_path)
        if duration is not None and duration > self.max_duration_seconds:
            output_path.unlink(missing_ok=True)
            raise SocialVideoDownloadError(
                f"Video dài {math.ceil(duration / 60)} phút, "
                f"quá giới hạn {self.max_duration_seconds // 60} phút"
            )
        return SocialVideoDownloadResult(
            path=output_path,
            source_url=canonical_google_drive_url(url),
            platform=GOOGLE_DRIVE_PLATFORM,
            title=Path(filename).stem if filename else "",
            video_id=file_id,
            duration=duration,
        )

    def download(
        self,
        url: str,
        destination_stem: Path,
        *,
        progress_callback: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
        cookie_file: Path | None = None,
    ) -> SocialVideoDownloadResult:
        normalized_url = normalize_social_video_url(url)
        if social_platform(normalized_url) == GOOGLE_DRIVE_PLATFORM:
            destination_stem = Path(destination_stem).resolve()
            destination_stem.parent.mkdir(parents=True, exist_ok=True)
            for stale in destination_stem.parent.glob(f"{destination_stem.name}.*"):
                if stale.is_file() and stale.suffix.lower() in DRIVE_VIDEO_EXTENSIONS | {".part"}:
                    stale.unlink(missing_ok=True)
            if cookie_file and not cookie_file.is_file():
                raise SocialVideoDownloadError(f"Không tìm thấy file cookies.txt đã cấu hình: {cookie_file}")
            return self._download_google_drive(
                normalized_url, destination_stem, progress_callback=progress_callback,
                cancel_requested=cancel_requested, cookie_file=cookie_file,
            )
        if social_platform(normalized_url) == "Douyin":
            normalized_url = self.canonical_douyin_url(normalized_url)
        xiaohongshu_note_type: str | None = None
        if social_platform(normalized_url) == XIAOHONGSHU_PLATFORM:
            normalized_url, xiaohongshu_note_type = self.canonical_xiaohongshu_url(normalized_url)
        destination_stem = Path(destination_stem).resolve()
        destination_stem.parent.mkdir(parents=True, exist_ok=True)

        def cleanup() -> None:
            for candidate in destination_stem.parent.glob(f"{destination_stem.name}.*"):
                is_download_artifact = (
                    candidate.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
                    or candidate.suffix.lower() in {".part", ".ytdl"}
                )
                if candidate.is_file() and candidate.parent == destination_stem.parent and is_download_artifact:
                    candidate.unlink(missing_ok=True)

        cleanup()

        # yt-dlp skips a video its match_filter rejects without raising, so the reason
        # is kept here and reported instead of a missing-file error.
        rejections: list[str] = []

        def match_filter(info: dict[str, Any], *, incomplete: bool = False) -> str | None:
            del incomplete
            reason = None
            if _has_no_video_track(info):
                if platform in {"TikTok", "Douyin"}:
                    # TikTok's web page can hide the video from a server IP and leave only
                    # the soundtrack mp3; raising sends the download to the API fallbacks.
                    raise _AudioOnlySource("yt-dlp found only an audio track")
                reason = (
                    "Link này là bài đăng ảnh hoặc chỉ có âm thanh, không có video. "
                    "Hãy dùng link một video"
                )
            duration = info.get("duration")
            if duration is not None and float(duration) > self.max_duration_seconds:
                reason = (
                    f"Video dài {math.ceil(float(duration) / 60)} phút, "
                    f"quá giới hạn {self.max_duration_seconds // 60} phút"
                )
            filesize = info.get("filesize") or info.get("filesize_approx")
            if reason is None and filesize is not None and int(filesize) > self.max_bytes:
                reason = f"Video lớn quá giới hạn {self.max_bytes // (1024 * 1024)} MB"
            if reason:
                rejections.append(reason)
            return reason

        def progress_hook(status: dict[str, Any]) -> None:
            if cancel_requested and cancel_requested():
                raise SocialVideoDownloadCancelled("Đã hủy khi đang tải video nguồn")
            downloaded = int(status.get("downloaded_bytes") or 0)
            total_value = status.get("total_bytes") or status.get("total_bytes_estimate")
            total = int(total_value) if total_value else None
            if downloaded > self.max_bytes or (total is not None and total > self.max_bytes):
                raise SocialVideoDownloadError(
                    f"Video lớn quá giới hạn {self.max_bytes // (1024 * 1024)} MB"
                )
            if progress_callback:
                progress_callback(downloaded, total)

        options: dict[str, Any] = {
            "format": "bestvideo*+bestaudio/best",
            "outtmpl": f"{destination_stem}.%(ext)s",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "max_filesize": self.max_bytes,
            "match_filter": match_filter,
            "progress_hooks": [progress_hook],
            "socket_timeout": self.socket_timeout,
            "retries": 3,
            "fragment_retries": 3,
            # A missing HLS/DASH fragment must fail, not produce a partial video.
            "skip_unavailable_fragments": False,
            "quiet": True,
            "no_warnings": True,
            "overwrites": True,
        }
        platform = social_platform(normalized_url)
        if platform == "Douyin":
            # Never let the generic extractor pick an unrelated video from the page;
            # unsupported forms go to the browser resolver, which matches the aweme id.
            options["allowed_extractors"] = ["douyin"]
        if platform == XIAOHONGSHU_PLATFORM:
            options["allowed_extractors"] = ["xiaohongshu"]
        if platform == "Douyin" and self.douyin_proxy:
            options["proxy"] = self.douyin_proxy
        elif platform == "TikTok" and self.tiktok_proxy:
            options["proxy"] = self.tiktok_proxy
        elif platform == "YouTube" and self.youtube_proxy:
            # Server-side downloaders spread YouTube traffic over other IPs;
            # a residential proxy does the same when the VPS IP gets
            # "Sign in to confirm you're not a bot". The guest retry below
            # copies these options, so it uses the proxy too.
            options["proxy"] = self.youtube_proxy

        if self.ffmpeg_location:
            options["ffmpeg_location"] = self.ffmpeg_location
        browser_cookie_path = destination_stem.with_name(destination_stem.name + ".browser.cookies.txt")
        # A per-job upload is authoritative. Otherwise prefer a fresh snapshot
        # from the isolated platform browser and keep the configured shared file
        # as a fallback for deployments that do not run browser sidecars.
        effective_cookie_file = cookie_file
        if effective_cookie_file and not effective_cookie_file.is_file():
            raise SocialVideoDownloadError(
                f"Không tìm thấy file cookies.txt đã cấu hình: {effective_cookie_file}"
            )
        if social_platform(normalized_url) == "Douyin" and self._douyin_cookie_provider and not effective_cookie_file:
            try:
                effective_cookie_file = self._douyin_cookie_provider(browser_cookie_path)
            except Exception as cookie_error:
                logger.warning("Cannot refresh Douyin download cookies: %s", type(cookie_error).__name__)
        platform_cookie_provider = self._platform_cookie_providers.get(platform)
        if platform_cookie_provider and not effective_cookie_file:
            try:
                effective_cookie_file = platform_cookie_provider(browser_cookie_path)
            except Exception as cookie_error:
                logger.warning("Cannot refresh %s download cookies: %s", platform, type(cookie_error).__name__)
        if not effective_cookie_file:
            effective_cookie_file = self.cookie_file
            if effective_cookie_file and not effective_cookie_file.is_file():
                raise SocialVideoDownloadError(
                    f"Không tìm thấy file cookies.txt đã cấu hình: {effective_cookie_file}"
                )
        if effective_cookie_file and effective_cookie_file.is_file():
            options["cookiefile"] = str(effective_cookie_file)
            user_agent_provider = self._platform_user_agent_providers.get(platform)
            if user_agent_provider:
                try:
                    browser_user_agent = str(user_agent_provider() or "").strip()
                    if browser_user_agent:
                        options["http_headers"] = {"User-Agent": browser_user_agent}
                except Exception as user_agent_error:
                    logger.warning(
                        "Cannot read %s browser user agent: %s",
                        platform,
                        type(user_agent_error).__name__,
                    )
            if platform == "YouTube":
                options["extractor_args"] = {
                    "youtube": {"player_client": YOUTUBE_AUTHENTICATED_PLAYER_CLIENTS}
                }

        try:
            if cancel_requested and cancel_requested():
                raise SocialVideoDownloadCancelled("Đã hủy khi đang tải video nguồn")
            try:
                with self._create_ydl(options) as ydl:
                    info = ydl.extract_info(normalized_url, download=True)
                    sanitized = ydl.sanitize_info(info) if hasattr(ydl, "sanitize_info") else dict(info or {})
            except Exception as authenticated_error:
                # A current YouTube regression can reject the authenticated
                # tv player while the exact same public video remains
                # downloadable as a guest. Browsers/media sniffers naturally
                # fall back to the public playback request, so mirror that
                # behaviour before declaring the login session invalid.
                should_retry_as_guest = (
                    platform == "YouTube"
                    and bool(effective_cookie_file)
                    and YOUTUBE_RELOAD_ERROR_MARKER in str(authenticated_error).lower()
                )
                if not should_retry_as_guest:
                    raise
                cleanup()
                guest_options = dict(options)
                guest_options.pop("cookiefile", None)
                guest_options.pop("extractor_args", None)
                guest_options.pop("http_headers", None)
                logger.info("Retrying public YouTube playback without account cookies")
                with self._create_ydl(guest_options) as ydl:
                    info = ydl.extract_info(normalized_url, download=True)
                    sanitized = ydl.sanitize_info(info) if hasattr(ydl, "sanitize_info") else dict(info or {})
        except SocialVideoDownloadCancelled:
            cleanup()
            raise
        except SocialVideoDownloadError:
            cleanup()
            raise
        except Exception as exc:
            cleanup()
            if cancel_requested and cancel_requested():
                raise SocialVideoDownloadCancelled("Đã hủy khi đang tải video nguồn") from exc
            # Facebook falls back to the page the logged-in browser renders, the
            # same media URL a browser downloader would capture.
            browser_fallback_platforms = {"TikTok", "Douyin"}
            if self._facebook_resolver is not None:
                browser_fallback_platforms.add("Facebook")
            if platform == XIAOHONGSHU_PLATFORM:
                raise SocialVideoDownloadError(_xiaohongshu_failure_message(
                    exc, used_cookies=bool(effective_cookie_file), note_type=xiaohongshu_note_type,
                )) from exc
            if platform not in browser_fallback_platforms:
                raise SocialVideoDownloadError(
                    _download_failure_message(platform, exc, used_cookies=bool(effective_cookie_file))
                ) from exc
            
            try:
                if platform == "Douyin":
                    if self._douyin_resolver is None:
                        raise RuntimeError("Douyin browser is not configured")
                    info = self._douyin_resolver(normalized_url, browser_cookie_path, cancel_requested)
                    if browser_cookie_path.is_file():
                        options["cookiefile"] = str(browser_cookie_path)
                elif platform == "Facebook":
                    info = self._facebook_resolver(normalized_url, browser_cookie_path, cancel_requested)
                    if browser_cookie_path.is_file():
                        options["cookiefile"] = str(browser_cookie_path)
                else:
                    # tikwm first: its hdplay is the original-resolution upload
                    # (often 1080p HEVC), while ssstik serves a smaller render.
                    info = None
                    try:
                        info = self._tikwm_info(normalized_url)
                    except SocialVideoDownloadError:
                        raise
                    except Exception as tikwm_err:
                        logger.warning("tikwm.com fallback failed: %s", tikwm_err)
                    if not info:
                        info = self._ssstik_info(normalized_url)
                    if not info:
                        raise RuntimeError("Both tikwm.com and ssstik.io API fallbacks failed")
                rejected = match_filter(info)
                if rejected:
                    raise SocialVideoDownloadError(rejected)
                progress_hook({})
                if info.get(SLIDESHOW_KEY):
                    sanitized = self._download_slideshow(
                        info, destination_stem, progress_callback=progress_callback,
                        cancel_requested=cancel_requested,
                    )
                else:
                    with self._create_ydl(options) as ydl:
                        downloaded_info = ydl.process_ie_result(info, download=True)
                        sanitized = ydl.sanitize_info(downloaded_info) if hasattr(ydl, "sanitize_info") else dict(downloaded_info or info)
            except SocialVideoDownloadError:
                cleanup()
                raise
            except Exception as fallback_error:
                cleanup()
                if cancel_requested and cancel_requested():
                    raise SocialVideoDownloadCancelled("Đã hủy khi đang tải video nguồn") from fallback_error
                logger.warning("Social video fallback failed: %s", type(fallback_error).__name__)
                message = " ".join(str(exc).split())
                if platform == "Douyin":
                    message = (
                        "Không lấy được video qua phiên trình duyệt. Mở tab Douyin, đăng nhập hoặc hoàn tất "
                        "xác minh rồi bấm Đồng bộ và thử lại. Nếu trình duyệt không mở được, kiểm tra dịch vụ douyin-browser."
                    )
                elif platform == "Facebook":
                    message = (
                        "video không công khai, đã bị xóa hoặc cần đăng nhập. Hãy đăng nhập Facebook trong "
                        "trình duyệt VidTrans bằng tài khoản xem được video, bấm Đồng bộ rồi thử lại"
                    )
                raise SocialVideoDownloadError(
                    f"Không tải được video {platform}: {message or 'nguồn từ chối truy cập'}"
                ) from exc
        finally:
            browser_cookie_path.unlink(missing_ok=True)

        candidates = sorted(
            (
                path
                for path in destination_stem.parent.glob(f"{destination_stem.name}.*")
                if path.is_file()
                and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
                # Only the final outtmpl file; exclude .f137.mp4/.temp.mp4.
                and path.stem == destination_stem.name
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            cleanup()
            if rejections:
                raise SocialVideoDownloadError(rejections[-1])
            raise SocialVideoDownloadError("Tải nguồn chưa hoàn tất: không tìm thấy file video đã ghép xong")
        output_path = candidates[0]
        if output_path.stat().st_size == 0:
            cleanup()
            raise SocialVideoDownloadError("File video tải về bị rỗng")
        rejected = match_filter(sanitized)
        if rejected:
            cleanup()
            raise SocialVideoDownloadError(rejected)
        if output_path.stat().st_size > self.max_bytes:
            cleanup()
            raise SocialVideoDownloadError(
                f"Video lớn quá giới hạn {self.max_bytes // (1024 * 1024)} MB"
            )
        raw_duration = sanitized.get("duration")
        duration = float(raw_duration) if raw_duration is not None else None
        return SocialVideoDownloadResult(
            path=output_path,
            source_url=normalized_url,
            platform=platform,
            title=str(sanitized.get("title") or "").strip(),
            video_id=str(sanitized.get("id")) if sanitized.get("id") is not None else None,
            duration=duration,
        )
