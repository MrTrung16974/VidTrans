from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ContextManager
from urllib.parse import urlsplit, urlunsplit


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
    return "Douyin" if hostname == "douyin.com" or hostname.endswith(".douyin.com") else "TikTok"


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
    ) -> None:
        self.ffmpeg_location = ffmpeg_location
        self.max_bytes = max_bytes
        self.max_duration_seconds = max_duration_seconds
        self.socket_timeout = socket_timeout
        self.cookie_file = cookie_file
        self._ydl_factory = ydl_factory

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
            "quiet": True,
            "no_warnings": True,
            "overwrites": True,
        }
        if self.ffmpeg_location:
            options["ffmpeg_location"] = self.ffmpeg_location
        effective_cookie_file = cookie_file or self.cookie_file
        if effective_cookie_file and effective_cookie_file.is_file():
            options["cookiefile"] = str(effective_cookie_file)

        try:
            with self._create_ydl(options) as ydl:
                info = ydl.extract_info(normalized_url, download=True)
                sanitized = ydl.sanitize_info(info) if hasattr(ydl, "sanitize_info") else dict(info or {})
        except SocialVideoDownloadCancelled:
            cleanup()
            raise
        except Exception as exc:
            cleanup()
            if cancel_requested and cancel_requested():
                raise SocialVideoDownloadCancelled("Đã hủy khi đang tải video nguồn") from exc
            message = " ".join(str(exc).split())
            if "fresh cookies" in message.lower():
                message = (
                    "Douyin yêu cầu cookie mới. Hãy xuất cookies.txt định dạng Netscape từ trình duyệt "
                    "đang mở được Douyin, rồi chọn file đó trong mục Link TikTok / Douyin."
                )
            raise SocialVideoDownloadError(
                f"Không tải được video {social_platform(normalized_url)}: {message or 'nguồn từ chối truy cập'}"
            ) from exc

        candidates = sorted(
            (
                path
                for path in destination_stem.parent.glob(f"{destination_stem.name}.*")
                if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            cleanup()
            raise SocialVideoDownloadError("yt-dlp không tạo được file video hợp lệ")
        output_path = candidates[0]
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
