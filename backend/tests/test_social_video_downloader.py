from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.social_video_downloader import (
    SocialVideoDownloadCancelled,
    SocialVideoDownloader,
    extract_social_video_urls,
    normalize_social_video_url,
    social_platform,
)


DOUYIN_SHARE_TEXT = (
    "7.17 Ymq:/ :7pm J@v.sR 06/05 为什么产屋敷耀哉，被称为最有魅力的领袖呢 "
    "# 青年创作者成长计划 https://v.douyin.com/GXgZS-F73fI/ "
    "复制此链接，打开Dou音搜索，直接观看视频！"
)


class FakeYoutubeDL:
    def __init__(self, options: dict, *, cancel: bool = False) -> None:
        self.options = options
        self.cancel = cancel

    def __enter__(self) -> "FakeYoutubeDL":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def extract_info(self, url: str, *, download: bool) -> dict:
        assert download is True
        output = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
        output.write_bytes(b"video")
        hook = self.options["progress_hooks"][0]
        hook({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 5})
        return {"id": "123", "title": "Video thử nghiệm", "duration": 12.5, "webpage_url": url}

    @staticmethod
    def sanitize_info(info: dict) -> dict:
        return dict(info)


class SocialVideoDownloaderTests(unittest.TestCase):
    def test_extracts_douyin_url_from_full_share_text(self) -> None:
        self.assertEqual(
            extract_social_video_urls(DOUYIN_SHARE_TEXT),
            ["https://v.douyin.com/GXgZS-F73fI/"],
        )

    def test_extracts_unique_tiktok_and_douyin_urls(self) -> None:
        text = (
            "https://vm.tiktok.com/ZM123/ https://www.tiktok.com/@creator/video/123?lang=vi "
            "https://vm.tiktok.com/ZM123/"
        )
        self.assertEqual(
            extract_social_video_urls(text),
            [
                "https://vm.tiktok.com/ZM123/",
                "https://www.tiktok.com/@creator/video/123?lang=vi",
            ],
        )

    def test_rejects_unsupported_or_unsafe_urls(self) -> None:
        invalid_urls = [
            "https://example.com/video/1",
            "file:///etc/passwd",
            "https://tiktok.com.evil.example/video/1",
            "https://user:secret@www.tiktok.com/video/1",
        ]
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_social_video_url(url)

    def test_platform_name_uses_allowlisted_hostname(self) -> None:
        self.assertEqual(social_platform("https://v.douyin.com/example/"), "Douyin")
        self.assertEqual(social_platform("https://vm.tiktok.com/example/"), "TikTok")

    def test_download_returns_downloaded_video_and_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            progress: list[tuple[int, int | None]] = []
            downloader = SocialVideoDownloader(
                ydl_factory=lambda options: FakeYoutubeDL(options),
                max_bytes=100,
            )

            result = downloader.download(
                "https://v.douyin.com/example/",
                tmp_path / "job123",
                progress_callback=lambda downloaded, total: progress.append((downloaded, total)),
            )

            self.assertEqual(result.path, tmp_path / "job123.mp4")
            self.assertEqual(result.display_filename, "Video thử nghiệm.mp4")
            self.assertEqual(result.platform, "Douyin")
            self.assertEqual(progress, [(5, 5)])

    def test_single_video_does_not_use_max_downloads_guard(self) -> None:
        """yt-dlp may raise MaxDownloadsReached after a successful first file."""

        with TemporaryDirectory() as directory:
            instances: list[FakeYoutubeDL] = []

            def factory(options: dict) -> FakeYoutubeDL:
                instance = FakeYoutubeDL(options)
                instances.append(instance)
                return instance

            downloader = SocialVideoDownloader(ydl_factory=factory)
            downloader.download("https://v.douyin.com/example/", Path(directory) / "single")

            self.assertTrue(instances[0].options["noplaylist"])
            self.assertNotIn("max_downloads", instances[0].options)

    def test_download_cancellation_removes_partial_file(self) -> None:
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            downloader = SocialVideoDownloader(ydl_factory=lambda options: FakeYoutubeDL(options))

            with self.assertRaises(SocialVideoDownloadCancelled):
                downloader.download(
                    "https://vm.tiktok.com/example/",
                    tmp_path / "job456",
                    cancel_requested=lambda: True,
                )

            self.assertEqual(list(tmp_path.iterdir()), [])

    def test_job_cookie_file_is_passed_to_yt_dlp_and_not_cleaned(self) -> None:
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            cookie_path = tmp_path / "job789.cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            instances: list[FakeYoutubeDL] = []

            def factory(options: dict) -> FakeYoutubeDL:
                instance = FakeYoutubeDL(options)
                instances.append(instance)
                return instance

            downloader = SocialVideoDownloader(ydl_factory=factory)
            downloader.download(
                "https://v.douyin.com/example/",
                tmp_path / "job789",
                cookie_file=cookie_path,
            )

            self.assertEqual(instances[0].options["cookiefile"], str(cookie_path))
            self.assertTrue(cookie_path.is_file())


if __name__ == "__main__":
    unittest.main()
