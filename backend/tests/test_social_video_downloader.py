from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from infrastructure.social_video_downloader import (
    SocialVideoDownloadCancelled,
    SocialVideoDownloadError,
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
        self.assertEqual(social_platform("https://www.iesdouyin.com/share/video/123"), "Douyin")

    def test_browser_fallback_recovers_cookie_error_and_cleans_private_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            stem = Path(directory) / "recover"
            first = FakeYoutubeDL({})
            first.extract_info = Mock(side_effect=RuntimeError("Fresh cookies are needed"))
            calls = []

            def factory(options):
                calls.append(dict(options))
                if len(calls) == 1:
                    return first
                ydl = FakeYoutubeDL(options)
                ydl.process_ie_result = lambda info, download: {**ydl.extract_info("direct", download=download), **info}
                return ydl

            def resolve(url, cookie_path, cancel):
                cookie_path.write_text("fresh cookies", encoding="utf-8")
                return {"id": "7661982102736473384", "title": "Recovered", "duration": 20,
                        "url": "https://v.douyinvod.com/video.mp4"}

            downloader = SocialVideoDownloader(ydl_factory=factory, douyin_resolver=resolve)
            with patch("urllib.request.urlopen", side_effect=AssertionError("No third-party API for Douyin")):
                result = downloader.download("https://www.douyin.com/video/7661982102736473384", stem)
            self.assertEqual(result.video_id, "7661982102736473384")
            self.assertEqual(result.path.read_bytes(), b"video")
            self.assertIn("cookiefile", calls[1])
            self.assertFalse(Path(calls[1]["cookiefile"]).exists())

    def test_browser_fallback_respects_duration_and_cancellation(self) -> None:
        for failure in ("duration", "cancel"):
            with self.subTest(failure=failure), TemporaryDirectory() as directory:
                ydl = FakeYoutubeDL({})
                ydl.extract_info = Mock(side_effect=RuntimeError("Fresh cookies are needed"))

                def resolve(*args):
                    if failure == "cancel":
                        raise SocialVideoDownloadCancelled("cancelled")
                    return {"id": "1", "duration": 99999}

                downloader = SocialVideoDownloader(ydl_factory=lambda options: ydl, douyin_resolver=resolve)
                error_type = SocialVideoDownloadCancelled if failure == "cancel" else SocialVideoDownloadError
                with self.assertRaises(error_type):
                    downloader.download("https://www.douyin.com/video/1", Path(directory) / "failed")
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_guest_cookie_snapshot_is_refreshed_per_download(self) -> None:
        with TemporaryDirectory() as directory:
            snapshots = []
            def provider(path):
                snapshots.append(path)
                path.write_text("guest cookies", encoding="utf-8")
                return path
            downloader = SocialVideoDownloader(ydl_factory=FakeYoutubeDL, douyin_cookie_provider=provider)
            for name in ("first", "second"):
                downloader.download("https://www.douyin.com/video/1", Path(directory) / name)
            self.assertEqual(len(snapshots), 2)
            self.assertNotEqual(*snapshots)
            self.assertTrue(all(not path.exists() for path in snapshots))

    def test_failed_browser_transfer_removes_partial_video_and_cookie(self) -> None:
        with TemporaryDirectory() as directory:
            stem = Path(directory) / "broken"
            attempts = []
            def factory(options):
                ydl = FakeYoutubeDL(options)
                attempts.append(ydl)
                ydl.extract_info = Mock(side_effect=RuntimeError("Fresh cookies are needed"))
                def fail_transfer(info, download):
                    stem.with_suffix(".mp4.part").write_bytes(b"partial")
                    raise RuntimeError("Connection reset")
                ydl.process_ie_result = fail_transfer
                return ydl
            def resolve(url, path, cancel):
                path.write_text("fresh cookies", encoding="utf-8")
                return {"id": "1", "duration": 1}
            downloader = SocialVideoDownloader(ydl_factory=factory, douyin_resolver=resolve)
            with self.assertRaises(SocialVideoDownloadError):
                downloader.download("https://www.douyin.com/video/1", stem)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_oversized_download_does_not_trigger_fallback(self) -> None:
        with TemporaryDirectory() as directory:
            resolver = Mock()
            downloader = SocialVideoDownloader(ydl_factory=FakeYoutubeDL, max_bytes=4, douyin_resolver=resolver)
            with self.assertRaisesRegex(SocialVideoDownloadError, "giới hạn"):
                downloader.download("https://www.douyin.com/video/1", Path(directory) / "large")
            resolver.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

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

            self.assertEqual(result.path, (tmp_path / "job123.mp4").resolve())
            self.assertEqual(result.display_filename, "Video thử nghiệm.mp4")
            self.assertEqual(result.platform, "Douyin")
            self.assertEqual(progress, [(5, 5)])

    def test_does_not_accept_unmerged_component_as_finished_video(self):
        with TemporaryDirectory() as directory:
            stem = Path(directory) / "partial"
            def factory(options):
                instance = FakeYoutubeDL(options)
                def extract(url, download):
                    stem.with_suffix(".f137.mp4").write_bytes(b"video track")
                    stem.with_suffix(".mp4.part").write_bytes(b"partial")
                    return {"id": "1", "duration": 10}
                instance.extract_info = extract
                return instance
            downloader = SocialVideoDownloader(ydl_factory=factory)
            with self.assertRaisesRegex(SocialVideoDownloadError, "chưa hoàn tất"):
                downloader.download("https://v.douyin.com/example/", stem)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_final_video_wins_over_newer_component(self):
        with TemporaryDirectory() as directory:
            stem = Path(directory) / "complete"
            def factory(options):
                self.assertFalse(options["skip_unavailable_fragments"])
                instance = FakeYoutubeDL(options)
                original = instance.extract_info
                def extract(url, download):
                    result = original(url, download=download)
                    stem.with_suffix(".f137.mp4").write_bytes(b"component")
                    return result
                instance.extract_info = extract
                return instance
            result = SocialVideoDownloader(ydl_factory=factory).download("https://v.douyin.com/example/", stem)
            self.assertEqual(result.path.name, "complete.mp4")
            self.assertEqual(result.path.read_bytes(), b"video")

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
