from __future__ import annotations

import importlib
import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ContextManager
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

logger = logging.getLogger(__name__)


SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
SUPPORTED_SOCIAL_DOMAINS = {
    "douyin.com",
    "iesdouyin.com",
    "tiktok.com",
}
URL_PATTERN = re.compile(r"https?://[^\s<>\[\]\"']+", re.IGNORECASE)
TRAILING_URL_PUNCTUATION = ".,;:!?)]}，。；：！？）】》、"


class SocialVideoDownloadError(RuntimeError):
    """A safe, user-facing failure while resolving or downloading a social video."""


class SocialVideoDownloadCancelled(SocialVideoDownloadError):
    pass


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
        raise ValueError("Link TikTok/Douyin không hợp lệ") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise ValueError("Link phải bắt đầu bằng http:// hoặc https://")
    if parsed.username or parsed.password or port not in {None, 80, 443}:
        raise ValueError("Link TikTok/Douyin chứa thông tin kết nối không được hỗ trợ")
    if not _is_supported_host(hostname):
        raise ValueError(f"Chưa hỗ trợ tải video từ tên miền {hostname}")
    scheme = "https" if parsed.scheme.lower() == "https" else "http"
    netloc = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


def extract_social_video_urls(share_text: str, *, limit: int = 50) -> list[str]:
    """Extract unique TikTok/Douyin links from URLs or full copied share messages."""

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
    return "Douyin" if any(hostname == domain or hostname.endswith("." + domain)
                           for domain in ("douyin.com", "iesdouyin.com")) else "TikTok"


ProgressCallback = Callable[[int, int | None], None]
CancelCallback = Callable[[], bool]
YoutubeDLFactory = Callable[[dict[str, Any]], ContextManager[Any]]


class SocialVideoDownloader:
    """Download one allow-listed TikTok/Douyin video through yt-dlp."""

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
        douyin_resolver: Callable[..., dict[str, Any]] | None = None,
        tiktok_proxy: str | None = None,
        douyin_proxy: str | None = None,
    ) -> None:
        self.ffmpeg_location = ffmpeg_location
        self.max_bytes = max_bytes
        self.max_duration_seconds = max_duration_seconds
        self.socket_timeout = socket_timeout
        self.cookie_file = cookie_file
        self._ydl_factory = ydl_factory
        self._douyin_cookie_provider = douyin_cookie_provider
        self._douyin_resolver = douyin_resolver
        self.tiktok_proxy = tiktok_proxy
        self.douyin_proxy = douyin_proxy

    def _create_ydl(self, options: dict[str, Any]) -> ContextManager[Any]:
        if self._ydl_factory is not None:
            return self._ydl_factory(options)
        try:
            yt_dlp = importlib.import_module("yt_dlp")
        except ImportError as exc:  # pragma: no cover - exercised by deployment checks
            raise SocialVideoDownloadError(
                "Thiếu yt-dlp. Hãy build lại Docker image để cài bộ tải TikTok/Douyin."
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
        if platform == "Douyin" and self.douyin_proxy:
            options["proxy"] = self.douyin_proxy
        elif platform == "TikTok" and self.tiktok_proxy:
            options["proxy"] = self.tiktok_proxy

        if self.ffmpeg_location:
            options["ffmpeg_location"] = self.ffmpeg_location
        browser_cookie_path = destination_stem.with_name(destination_stem.name + ".browser.cookies.txt")
        effective_cookie_file = cookie_file or self.cookie_file
        if social_platform(normalized_url) == "Douyin" and self._douyin_cookie_provider and not effective_cookie_file:
            try:
                effective_cookie_file = self._douyin_cookie_provider(browser_cookie_path)
            except Exception as cookie_error:
                logger.warning("Cannot refresh Douyin download cookies: %s", type(cookie_error).__name__)
        if effective_cookie_file and effective_cookie_file.is_file():
            options["cookiefile"] = str(effective_cookie_file)

        try:
            if cancel_requested and cancel_requested():
                raise SocialVideoDownloadCancelled("Đã hủy khi đang tải video nguồn")
            with self._create_ydl(options) as ydl:
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
            
            try:
                if social_platform(normalized_url) == "Douyin":
                    if self._douyin_resolver is None:
                        raise RuntimeError("Douyin browser is not configured")
                    info = self._douyin_resolver(normalized_url, browser_cookie_path, cancel_requested)
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
                if social_platform(normalized_url) == "Douyin":
                    message = (
                        "Không lấy được video qua phiên trình duyệt. Mở tab Douyin, đăng nhập hoặc hoàn tất "
                        "xác minh rồi bấm Đồng bộ và thử lại. Nếu trình duyệt không mở được, kiểm tra dịch vụ douyin-browser."
                    )
                raise SocialVideoDownloadError(
                    f"Không tải được video {social_platform(normalized_url)}: {message or 'nguồn từ chối truy cập'}"
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
            platform=social_platform(normalized_url),
            title=str(sanitized.get("title") or "").strip(),
            video_id=str(sanitized.get("id")) if sanitized.get("id") is not None else None,
            duration=duration,
        )
