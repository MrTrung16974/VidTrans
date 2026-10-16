from __future__ import annotations

import importlib
import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ContextManager
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit

logger = logging.getLogger(__name__)


SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
SUPPORTED_SOCIAL_DOMAINS = {
    "douyin.com",
    "facebook.com",
    "fb.com",
    "fb.watch",
    "iesdouyin.com",
    "tiktok.com",
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


def extract_social_video_urls(share_text: str, *, limit: int = 50) -> list[str]:
    """Extract unique allow-listed social video links from URLs or copied share messages."""

    candidates = URL_PATTERN.findall(share_text or "")
    if not candidates:
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = normalize_social_video_url(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
        if len(urls) > limit:
            raise ValueError(f"Chỉ được nhập tối đa {limit} link trong một batch")
    return urls


def social_platform(url: str) -> str:
    hostname = (urlsplit(url).hostname or "").lower()
    platform_domains = (
        ("Douyin", ("douyin.com", "iesdouyin.com")),
        ("TikTok", ("tiktok.com",)),
        ("YouTube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
        ("Facebook", ("facebook.com", "fb.com", "fb.watch")),
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


ProgressCallback = Callable[[int, int | None], None]
CancelCallback = Callable[[], bool]
YoutubeDLFactory = Callable[[dict[str, Any]], ContextManager[Any]]


class SocialVideoDownloader:
    """Download one allow-listed social video through yt-dlp."""

    def __init__(
        self,
        *,
        ffmpeg_location: str | None = None,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
        max_duration_seconds: int = 2 * 60 * 60,
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
    ) -> None:
        self.ffmpeg_location = ffmpeg_location
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
        if social_platform(normalized_url) == "Douyin":
            normalized_url = self.canonical_douyin_url(normalized_url)
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

        def match_filter(info: dict[str, Any], *, incomplete: bool = False) -> str | None:
            del incomplete
            duration = info.get("duration")
            if duration is not None and float(duration) > self.max_duration_seconds:
                return f"Video dài quá giới hạn {self.max_duration_seconds // 60} phút"
            filesize = info.get("filesize") or info.get("filesize_approx")
            if filesize is not None and int(filesize) > self.max_bytes:
                return f"Video lớn quá giới hạn {self.max_bytes // (1024 * 1024)} MB"
            return None

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
                    info = None
                    # Try ssstik.io first (more reliable)
                    try:
                        req_url = "https://ssstik.io/abc?url=dl"
                        req_data = urlencode({"id": normalized_url, "locale": "en", "tt": "1"}).encode('utf-8')
                        request = urllib.request.Request(req_url, data=req_data, headers={
                            "User-Agent": "Mozilla/5.0",
                            "Content-Type": "application/x-www-form-urlencoded",
                            "Hx-Request": "true"
                        })
                        with urllib.request.urlopen(request, timeout=self.socket_timeout) as response:
                            html = response.read(10 * 1024 * 1024).decode('utf-8', errors='ignore')
                        match = re.search(r'class="[^"]*download_link[^"]*".*?href="([^"#]+)"', html)
                        if not match:
                            match = re.search(r'class="[^"]*download_link[^"]*".*?data-directurl="([^"#]+)"', html)
                        if match:
                            info = {
                                "id": "video",
                                "title": "Video TikTok",
                                "duration": None,
                                "ext": "mp4",
                                "url": match.group(1).replace('&amp;', '&'),
                                "http_headers": {"Referer": "https://ssstik.io/"}
                            }
                    except Exception as ssstik_err:
                        logger.warning("ssstik.io fallback failed: %s", ssstik_err)

                    # Try tikwm.com if ssstik.io failed
                    if not info:
                        request = urllib.request.Request(
                            f"https://www.tikwm.com/api/?url={quote(normalized_url, safe='')}&hd=1",
                            headers={"User-Agent": "Mozilla/5.0"},
                        )
                        with urllib.request.urlopen(request, timeout=self.socket_timeout) as response:
                            payload = json.loads(response.read(2 * 1024 * 1024))
                        data = payload.get("data") or {}
                        if payload.get("code") != 0 or not data.get("play"):
                            raise RuntimeError("Both ssstik.io and tikwm.com API fallbacks failed")
                        info = {"id": str(data.get("id") or "video"), "title": data.get("title") or "Video TikTok",
                                "duration": data.get("duration"), "ext": "mp4",
                                "url": urljoin("https://www.tikwm.com/", data["play"])}
                rejected = match_filter(info)
                if rejected:
                    raise SocialVideoDownloadError(rejected)
                progress_hook({})
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
