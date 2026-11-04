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
    google_drive_file_id,
    normalize_social_video_url,
    social_platform,
    social_video_download_issue,
    xiaohongshu_note_url,
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

    def test_extracts_youtube_and_facebook_video_urls(self) -> None:
        text = (
            "https://youtu.be/abc123 https://www.youtube.com/shorts/short123 "
            "https://www.facebook.com/reel/987654 https://fb.watch/example/"
        )
        self.assertEqual(
            extract_social_video_urls(text),
            [
                "https://youtu.be/abc123",
                "https://www.youtube.com/shorts/short123",
                "https://www.facebook.com/reel/987654",
                "https://fb.watch/example/",
            ],
        )
    def test_accepts_only_links_with_a_download_method(self) -> None:
        downloadable = [
            "https://www.tiktok.com/@a/video/1",
            "https://www.tiktok.com/@a/photo/88",
            "https://vm.tiktok.com/ZM123/",
            "https://www.douyin.com/video/7661982102736473384",
            "https://www.douyin.com/jingxuan?modal_id=7661982102736473384",
            "https://www.iesdouyin.com/share/video/123",
            "https://v.douyin.com/abc/",
            "https://www.youtube.com/watch?v=abcdefghijk",
            "https://www.youtube.com/shorts/short123",
            "https://youtu.be/abc123",
            "https://www.facebook.com/reel/987654",
            "https://www.facebook.com/cnn/videos/10155529876156509/",
            "https://www.facebook.com/watch/?v=274175099429670",
            "https://www.facebook.com/share/v/1AbC/",
            "https://fb.watch/abc/",
        ]
        for url in downloadable:
            with self.subTest(url=url):
                self.assertIsNone(social_video_download_issue(normalize_social_video_url(url)))
        not_downloadable = [
            "https://www.tiktok.com/",
            "https://www.tiktok.com/@creator",
            "https://www.douyin.com/",
            "https://www.iesdouyin.com/share/note/7661982102736473384/",
            "https://www.youtube.com/",
            "https://www.youtube.com/@channel",
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.facebook.com/",
            "https://www.facebook.com/cnn",
            "https://www.facebook.com/watch/",
        ]
        for url in not_downloadable:
            with self.subTest(url=url):
                self.assertIsNotNone(social_video_download_issue(normalize_social_video_url(url)))

    def test_batch_reports_every_link_without_a_download_method(self) -> None:
        with self.assertRaises(ValueError) as caught:
            extract_social_video_urls(
                "https://www.tiktok.com/@a/video/1 https://www.youtube.com/@channel https://www.facebook.com/cnn"
            )
        message = str(caught.exception)
        self.assertIn("Có 2 link không thể tải", message)
        self.assertIn("https://www.youtube.com/@channel", message)
        self.assertIn("https://www.facebook.com/cnn", message)

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
        self.assertEqual(social_platform("https://youtu.be/example"), "YouTube")
        self.assertEqual(social_platform("https://www.youtube.com/watch?v=example"), "YouTube")
        self.assertEqual(social_platform("https://www.facebook.com/reel/123"), "Facebook")
        self.assertEqual(social_platform("https://fb.watch/example/"), "Facebook")

    def test_youtube_and_facebook_use_yt_dlp_without_tiktok_fallback(self) -> None:
        for url, platform in (
            ("https://youtu.be/example", "YouTube"),
            ("https://www.facebook.com/reel/123", "Facebook"),
        ):
            with self.subTest(platform=platform), TemporaryDirectory() as directory:
                result = SocialVideoDownloader(ydl_factory=FakeYoutubeDL).download(
                    url, Path(directory) / "video"
                )
                self.assertEqual(result.platform, platform)

    def test_youtube_and_facebook_fail_without_calling_tiktok_fallback_services(self) -> None:
        for url, platform in (
            ("https://youtu.be/example", "YouTube"),
            ("https://www.facebook.com/reel/123", "Facebook"),
        ):
            with self.subTest(platform=platform), TemporaryDirectory() as directory:
                ydl = FakeYoutubeDL({})
                ydl.extract_info = Mock(side_effect=RuntimeError("login required"))
                downloader = SocialVideoDownloader(ydl_factory=lambda options: ydl)
                with patch("urllib.request.urlopen", side_effect=AssertionError("TikTok fallback must not run")):
                    with self.assertRaisesRegex(SocialVideoDownloadError, platform):
                        downloader.download(url, Path(directory) / "video")

    def test_facebook_falls_back_to_browser_player_media(self) -> None:
        with TemporaryDirectory() as directory:
            calls = []

            def factory(options):
                ydl = FakeYoutubeDL(options)
                calls.append(ydl)
                if len(calls) == 1:
                    ydl.extract_info = Mock(side_effect=RuntimeError("Cannot parse data"))
                else:
                    def process_ie_result(info, *, download):
                        Path(options["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"video")
                        return info
                    ydl.process_ie_result = process_ie_result
                return ydl

            resolver = Mock(return_value={
                "id": "123", "title": "Video Facebook", "duration": None, "ext": "mp4",
                "url": "https://video.xx.fbcdn.net/v/123.mp4",
            })
            result = SocialVideoDownloader(ydl_factory=factory, facebook_resolver=resolver).download(
                "https://www.facebook.com/reel/123", Path(directory) / "video"
            )

            self.assertEqual(result.platform, "Facebook")
            self.assertEqual(result.title, "Video Facebook")
            self.assertEqual(resolver.call_args.args[0], "https://www.facebook.com/reel/123")

    def test_facebook_browser_fallback_failure_explains_access(self) -> None:
        with TemporaryDirectory() as directory:
            ydl = FakeYoutubeDL({})
            ydl.extract_info = Mock(side_effect=RuntimeError("Cannot parse data; please report this issue"))
            downloader = SocialVideoDownloader(
                ydl_factory=lambda options: ydl,
                facebook_resolver=Mock(side_effect=RuntimeError("no media")),
            )
            with self.assertRaises(SocialVideoDownloadError) as raised:
                downloader.download("https://www.facebook.com/reel/123", Path(directory) / "video")
            self.assertIn("đăng nhập Facebook", str(raised.exception))
            self.assertNotIn("report this issue", str(raised.exception))

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

    def test_youtube_bot_check_has_actionable_message_without_raw_yt_dlp_error(self) -> None:
        with TemporaryDirectory() as directory:
            ydl = FakeYoutubeDL({})
            ydl.extract_info = Mock(
                side_effect=RuntimeError("ERROR: Sign in to confirm you’re not a bot. Use --cookies")
            )
            downloader = SocialVideoDownloader(ydl_factory=lambda options: ydl)

            with self.assertRaises(SocialVideoDownloadError) as raised:
                downloader.download("https://youtu.be/example", Path(directory) / "video")

            self.assertIn("cookies.txt", str(raised.exception))
            self.assertNotIn("ERROR:", str(raised.exception))

    def test_youtube_bot_check_with_cookies_explains_vps_proxy_pair(self) -> None:
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "youtube.cookies.txt"
            snapshot.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            ydl = FakeYoutubeDL({})
            ydl.extract_info = Mock(
                side_effect=RuntimeError("Sign in to confirm you're not a bot")
            )
            downloader = SocialVideoDownloader(
                cookie_file=snapshot,
                ydl_factory=lambda options: ydl,
            )

            with self.assertRaises(SocialVideoDownloadError) as raised:
                downloader.download("https://youtu.be/example", Path(directory) / "video")

            self.assertIn("VIDTRANS_SOCIAL_BROWSER_PROXY", str(raised.exception))
            self.assertIn("VIDTRANS_YOUTUBE_PROXY", str(raised.exception))

    def test_configured_cookie_file_must_exist(self) -> None:
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-cookies.txt"
            downloader = SocialVideoDownloader(cookie_file=missing, ydl_factory=FakeYoutubeDL)

            with self.assertRaisesRegex(SocialVideoDownloadError, "Không tìm thấy file cookies.txt"):
                downloader.download("https://youtu.be/example", Path(directory) / "video")

    def test_platform_browser_cookie_provider_is_used_and_snapshot_is_deleted(self) -> None:
        with TemporaryDirectory() as directory:
            snapshot = (Path(directory) / "video.browser.cookies.txt").resolve()
            provider_paths = []

            def provider(path: Path) -> Path:
                provider_paths.append(path)
                path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
                return path

            instances = []

            def factory(options):
                instances.append(FakeYoutubeDL(options))
                return instances[-1]

            downloader = SocialVideoDownloader(
                ydl_factory=factory,
                platform_cookie_providers={"YouTube": provider},
            )
            downloader.download("https://youtu.be/example", Path(directory) / "video")

            self.assertEqual(provider_paths, [snapshot])
            self.assertEqual(instances[0].options["cookiefile"], str(snapshot))
            self.assertEqual(
                instances[0].options["extractor_args"],
                {"youtube": {"player_client": ["default", "-tv_downgraded", "web_embedded"]}},
            )
            self.assertFalse(snapshot.exists())

    def test_youtube_reload_error_is_reported_as_a_player_problem(self) -> None:
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "youtube.cookies.txt"
            snapshot.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            ydl = FakeYoutubeDL({})
            ydl.extract_info = Mock(side_effect=RuntimeError("ERROR: The page needs to be reloaded."))
            downloader = SocialVideoDownloader(cookie_file=snapshot, ydl_factory=lambda options: ydl)

            with self.assertRaises(SocialVideoDownloadError) as raised:
                downloader.download("https://youtu.be/example", Path(directory) / "video")

            self.assertIn("YouTube từ chối player tải tự động", str(raised.exception))
            self.assertNotIn("ERROR:", str(raised.exception))

    def test_youtube_reload_error_retries_public_playback_without_cookies(self) -> None:
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "youtube.cookies.txt"
            snapshot.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            calls = []

            def factory(options):
                calls.append(dict(options))
                ydl = FakeYoutubeDL(options)
                if len(calls) == 1:
                    ydl.extract_info = Mock(side_effect=RuntimeError("The page needs to be reloaded"))
                return ydl

            result = SocialVideoDownloader(cookie_file=snapshot, ydl_factory=factory).download(
                "https://youtu.be/example", Path(directory) / "video"
            )

            self.assertEqual(result.platform, "YouTube")
            self.assertIn("cookiefile", calls[0])
            self.assertNotIn("cookiefile", calls[1])
            self.assertNotIn("extractor_args", calls[1])

    def test_youtube_proxy_applies_only_to_youtube_and_survives_guest_retry(self) -> None:
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "youtube.cookies.txt"
            snapshot.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            calls = []

            def factory(options):
                calls.append(dict(options))
                ydl = FakeYoutubeDL(options)
                if len(calls) == 1:
                    ydl.extract_info = Mock(side_effect=RuntimeError("The page needs to be reloaded"))
                return ydl

            downloader = SocialVideoDownloader(
                cookie_file=snapshot, ydl_factory=factory, youtube_proxy="socks5://proxy.example:1080"
            )
            downloader.download("https://youtu.be/example", Path(directory) / "video")
            self.assertEqual([call.get("proxy") for call in calls], ["socks5://proxy.example:1080"] * 2)

            facebook_calls = []

            def facebook_factory(options):
                facebook_calls.append(dict(options))
                return FakeYoutubeDL(options)

            SocialVideoDownloader(ydl_factory=facebook_factory, youtube_proxy="socks5://proxy.example:1080").download(
                "https://www.facebook.com/reel/123", Path(directory) / "facebook"
            )
            self.assertNotIn("proxy", facebook_calls[0])

    def test_authenticated_download_uses_interactive_browser_user_agent(self) -> None:
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "youtube.cookies.txt"
            snapshot.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            instances = []

            def factory(options):
                instances.append(FakeYoutubeDL(options))
                return instances[-1]

            downloader = SocialVideoDownloader(
                cookie_file=snapshot,
                ydl_factory=factory,
                platform_user_agent_providers={"YouTube": lambda: "Browser UA"},
            )
            downloader.download("https://youtu.be/example", Path(directory) / "video")

            self.assertEqual(instances[0].options["http_headers"]["User-Agent"], "Browser UA")

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
                redirect_resolver=lambda url: "https://www.iesdouyin.com/share/video/7661982102736473384/?region=CN",
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

    def test_video_skipped_by_the_length_limit_reports_the_limit(self):
        # yt-dlp does not raise when its match_filter rejects a video; it just downloads nothing.
        with TemporaryDirectory() as directory:
            stem = Path(directory) / "long"

            def factory(options):
                instance = FakeYoutubeDL(options)

                def extract(url, download):
                    info = {"id": "1", "duration": 18271.8}
                    self.assertIsNotNone(options["match_filter"](info))
                    return info

                instance.extract_info = extract
                return instance

            downloader = SocialVideoDownloader(ydl_factory=factory, max_duration_seconds=7200)
            with self.assertRaisesRegex(SocialVideoDownloadError, "Video dài 305 phút, quá giới hạn 120 phút"):
                downloader.download("https://www.facebook.com/reel/834164483086130", stem)
            self.assertEqual(list(Path(directory).iterdir()), [])

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



class DouyinCanonicalUrlTests(unittest.TestCase):
    VIDEO = "https://www.douyin.com/video/7661982102736473384"

    def downloader(self, redirect="https://www.douyin.com/"):
        return SocialVideoDownloader(redirect_resolver=Mock(return_value=redirect))

    def test_modal_and_share_links_map_to_video_page_without_network(self) -> None:
        downloader = self.downloader()
        for url in (
            "https://www.douyin.com/jingxuan?modal_id=7661982102736473384",
            "https://www.douyin.com/user/MS4wLjABAAAA?from_tab_name=main&modal_id=7661982102736473384",
            "https://www.iesdouyin.com/share/video/7661982102736473384/?region=CN",
            self.VIDEO + "?previous_page=app_code_link",
        ):
            self.assertEqual(downloader.canonical_douyin_url(url), self.VIDEO, url)
        downloader._redirect_resolver.assert_not_called()

    def test_short_link_is_resolved_to_requested_video(self) -> None:
        downloader = self.downloader("https://www.iesdouyin.com/share/video/7661982102736473384/?u_code=x")
        self.assertEqual(downloader.canonical_douyin_url("https://v.douyin.com/GXgZS-F73fI/"), self.VIDEO)

    def test_image_post_is_rejected_instead_of_downloading_music(self) -> None:
        downloader = self.downloader("https://www.iesdouyin.com/share/note/7661982102736473384/")
        with self.assertRaises(SocialVideoDownloadError):
            downloader.canonical_douyin_url("https://v.douyin.com/abc/")

    def test_yt_dlp_receives_canonical_url_and_no_generic_extractor(self) -> None:
        seen = {}

        class Recorder(FakeYoutubeDL):
            def extract_info(self, url, *, download):
                seen["url"], seen["allowed"] = url, self.options.get("allowed_extractors")
                return super().extract_info(url, download=download)

        with TemporaryDirectory() as directory:
            downloader = SocialVideoDownloader(ydl_factory=Recorder, redirect_resolver=Mock())
            result = downloader.download(
                "https://www.douyin.com/jingxuan?modal_id=7661982102736473384", Path(directory) / "job"
            )
        self.assertEqual(seen, {"url": self.VIDEO, "allowed": ["douyin"]})
        self.assertEqual(result.source_url, self.VIDEO)


class AudioOnlyYoutubeDL:
    """yt-dlp that resolves a TikTok post to its soundtrack only, as it does from a server IP."""

    processed: list[dict] = []

    def __init__(self, options: dict) -> None:
        self.options = options

    def __enter__(self) -> "AudioOnlyYoutubeDL":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def extract_info(self, url: str, *, download: bool) -> dict:
        info = {"id": "1", "ext": "mp3", "vcodec": "none", "formats": [{"format_id": "audio", "ext": "mp3", "vcodec": "none"}]}
        reason = self.options["match_filter"](info)
        if reason:
            return {}  # yt-dlp skips the entry and downloads nothing
        raise AssertionError("audio-only info must be filtered")

    def process_ie_result(self, info: dict, *, download: bool) -> dict:
        AudioOnlyYoutubeDL.processed.append(info)
        Path(self.options["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"video")
        return info

    @staticmethod
    def sanitize_info(info: dict) -> dict:
        return dict(info)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self.body


def fake_urlopen(tikwm_data: dict):
    import json as _json

    def opener(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if url.startswith("https://www.tikwm.com/api/"):
            return FakeResponse(_json.dumps({"code": 0, "data": tikwm_data}).encode())
        if "ssstik" in url:
            raise AssertionError("ssstik must not run when tikwm answers")
        return FakeResponse(b"bytes-of-" + url.encode())

    return opener


class AudioOnlyFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        AudioOnlyYoutubeDL.processed = []

    def test_audio_only_tiktok_video_downloads_tikwm_hd_file(self) -> None:
        data = {"id": "77", "title": "Clip", "duration": 15, "play": "https://cdn/sd.mp4", "hdplay": "https://cdn/hd.mp4"}
        with TemporaryDirectory() as directory, patch("urllib.request.urlopen", side_effect=fake_urlopen(data)):
            result = SocialVideoDownloader(ydl_factory=AudioOnlyYoutubeDL).download(
                "https://www.tiktok.com/@a/video/77", Path(directory) / "job"
            )
        self.assertEqual(AudioOnlyYoutubeDL.processed[0]["url"], "https://cdn/hd.mp4")
        self.assertEqual(result.path.suffix, ".mp4")

    def test_tiktok_photo_post_is_rendered_into_a_video(self) -> None:
        data = {"id": "88", "title": "Ảnh", "images": ["https://cdn/1.jpeg", "https://cdn/2.jpeg"], "music": "https://cdn/m.mp3"}
        calls = []

        def fake_render(ffmpeg, images, audio, output, **kwargs):
            calls.append(([path.read_bytes() for path in images], audio.read_bytes() if audio else None))
            Path(output).write_bytes(b"slideshow")
            return 6.0

        with TemporaryDirectory() as directory, \
                patch("urllib.request.urlopen", side_effect=fake_urlopen(data)), \
                patch("infrastructure.social_video_downloader.render_slideshow", side_effect=fake_render):
            stem = Path(directory) / "job"
            result = SocialVideoDownloader(ydl_factory=AudioOnlyYoutubeDL).download(
                "https://www.tiktok.com/@a/photo/88", stem
            )
            self.assertEqual(result.path, (Path(directory) / "job.mp4").resolve())
            self.assertEqual(result.path.read_bytes(), b"slideshow")
            self.assertEqual(result.duration, 6.0)
            # The scratch folder with the raw images is removed.
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["job.mp4"])
        self.assertEqual(calls, [([b"bytes-of-https://cdn/1.jpeg", b"bytes-of-https://cdn/2.jpeg"], b"bytes-of-https://cdn/m.mp3")])
        self.assertEqual(AudioOnlyYoutubeDL.processed, [])

    def test_audio_only_youtube_reports_missing_video(self) -> None:
        with TemporaryDirectory() as directory, \
                patch("urllib.request.urlopen", side_effect=AssertionError("no TikTok fallback for YouTube")):
            with self.assertRaisesRegex(SocialVideoDownloadError, "không có video"):
                SocialVideoDownloader(ydl_factory=AudioOnlyYoutubeDL).download(
                    "https://www.youtube.com/watch?v=abcdefghijk", Path(directory) / "job"
                )


class SlideshowCommandTests(unittest.TestCase):
    def test_command_fits_every_image_and_loops_music(self) -> None:
        from infrastructure.tiktok_slideshow import build_slideshow_command

        command = build_slideshow_command("ffmpeg", [Path("a.jpg"), Path("b.jpg")], Path("m.mp3"), Path("o.mp4"))
        self.assertEqual(command.count("-loop"), 2)
        self.assertIn("-stream_loop", command)
        self.assertEqual(command[command.index("-t", command.index("-filter_complex")) + 1], "6.000")
        self.assertIn("+faststart", command)

    def test_command_without_music_adds_silence(self) -> None:
        from infrastructure.tiktok_slideshow import build_slideshow_command

        command = build_slideshow_command("ffmpeg", [Path("a.jpg")], None, Path("o.mp4"))
        self.assertIn("anullsrc=r=44100:cl=stereo", command)


DRIVE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


class FakeDriveResponse:
    def __init__(self, body: bytes, headers: dict[str, str], url: str = "https://drive.usercontent.google.com/download") -> None:
        self._body = body
        self._offset = 0
        self.headers = headers
        self._url = url

    def __enter__(self) -> "FakeDriveResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        end = len(self._body) if size < 0 else self._offset + size
        chunk = self._body[self._offset:end]
        self._offset += len(chunk)
        return chunk

    def geturl(self) -> str:
        return self._url


class FakeDriveOpener:
    def __init__(self, responses: list[FakeDriveResponse]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def open(self, request, timeout: int):  # noqa: ANN001
        self.urls.append(request.full_url)
        return self.responses.pop(0)


def drive_downloader(opener: FakeDriveOpener) -> SocialVideoDownloader:
    return SocialVideoDownloader(ffprobe_location="/nonexistent/ffprobe", http_opener_factory=lambda jar: opener)


VIDEO_HEADERS = {
    "Content-Type": "video/mp4",
    "Content-Disposition": "attachment; filename=\"clip.mp4\"; filename*=UTF-8''Phim%20hay.mp4",
    "Content-Length": "11",
}


class GoogleDriveTests(unittest.TestCase):
    def test_accepts_drive_file_links_and_rejects_folders_and_docs(self) -> None:
        for url in [
            f"https://drive.google.com/file/d/{DRIVE_ID}/view?usp=sharing",
            f"https://drive.google.com/file/u/0/d/{DRIVE_ID}/view",
            f"https://drive.google.com/open?id={DRIVE_ID}",
            f"https://drive.google.com/uc?id={DRIVE_ID}&export=download",
            f"https://docs.google.com/file/d/{DRIVE_ID}/edit",
            f"https://drive.usercontent.google.com/download?id={DRIVE_ID}&export=download",
        ]:
            with self.subTest(url=url):
                normalized = normalize_social_video_url(url)
                self.assertEqual(social_platform(normalized), "Google Drive")
                self.assertIsNone(social_video_download_issue(normalized))
                self.assertEqual(google_drive_file_id(normalized), DRIVE_ID)
        for url, reason in [
            ("https://drive.google.com/drive/folders/1xyzxyzxyzxyz", "thư mục"),
            ("https://drive.google.com/drive/u/0/folders/1xyzxyzxyzxyz", "thư mục"),
            ("https://docs.google.com/document/d/1xyzxyzxyzxyz/edit", "Google Docs"),
            ("https://drive.google.com/drive/my-drive", "mã file"),
        ]:
            with self.subTest(url=url):
                self.assertIn(reason, social_video_download_issue(normalize_social_video_url(url)))

    def test_batch_dedupes_drive_links_to_one_canonical_form(self) -> None:
        urls = extract_social_video_urls(
            f"https://drive.google.com/file/d/{DRIVE_ID}/view?usp=sharing "
            f"https://drive.google.com/open?id={DRIVE_ID} "
            f"https://drive.google.com/file/d/{DRIVE_ID}/view?resourcekey=0-key"
        )
        self.assertEqual(urls, [
            f"https://drive.google.com/file/d/{DRIVE_ID}/view",
            f"https://drive.google.com/file/d/{DRIVE_ID}/view?resourcekey=0-key",
        ])

    def test_downloads_the_original_file_with_progress(self) -> None:
        opener = FakeDriveOpener([FakeDriveResponse(b"video-bytes", VIDEO_HEADERS)])
        progress: list[tuple[int, int | None]] = []
        with TemporaryDirectory() as directory:
            result = drive_downloader(opener).download(
                f"https://drive.google.com/file/d/{DRIVE_ID}/view?resourcekey=0-key",
                Path(directory) / "source",
                progress_callback=lambda done, total: progress.append((done, total)),
            )
            self.assertEqual(result.path.read_bytes(), b"video-bytes")
            self.assertEqual(result.path.name, "source.mp4")
            self.assertEqual(result.platform, "Google Drive")
            self.assertEqual(result.title, "Phim hay")
            self.assertEqual(result.video_id, DRIVE_ID)
            self.assertEqual([path.name for path in Path(directory).iterdir()], ["source.mp4"])
        self.assertIn(f"id={DRIVE_ID}", opener.urls[0])
        self.assertIn("resourcekey=0-key", opener.urls[0])
        self.assertEqual(progress[-1], (11, 11))

    def test_follows_the_virus_scan_confirmation_form(self) -> None:
        confirm_page = (
            '<html><form id="download-form" action="https://drive.usercontent.google.com/download" method="get">'
            f'<input type="hidden" name="id" value="{DRIVE_ID}"><input type="hidden" name="export" value="download">'
            '<input type="hidden" name="confirm" value="t"><input type="hidden" name="uuid" value="u-123"></form></html>'
        ).encode()
        opener = FakeDriveOpener([
            FakeDriveResponse(confirm_page, {"Content-Type": "text/html; charset=utf-8"}),
            FakeDriveResponse(b"video-bytes", VIDEO_HEADERS),
        ])
        with TemporaryDirectory() as directory:
            result = drive_downloader(opener).download(
                f"https://drive.google.com/file/d/{DRIVE_ID}/view", Path(directory) / "source"
            )
            self.assertEqual(result.path.read_bytes(), b"video-bytes")
        self.assertIn("uuid=u-123", opener.urls[1])

    def test_private_quota_and_non_video_files_fail_with_a_clear_message(self) -> None:
        cases = [
            (FakeDriveResponse(b"<html>ServiceLogin</html>", {"Content-Type": "text/html"},
                               url="https://accounts.google.com/ServiceLogin"), "chưa chia sẻ công khai"),
            (FakeDriveResponse(b"<html>Download quota exceeded</html>", {"Content-Type": "text/html"}), "hạn mức"),
            (FakeDriveResponse(b"%PDF", {"Content-Type": "application/pdf",
                                         "Content-Disposition": 'attachment; filename="bai.pdf"'}), "không phải video"),
        ]
        for response, message in cases:
            with self.subTest(message=message), TemporaryDirectory() as directory:
                with self.assertRaises(SocialVideoDownloadError) as caught:
                    drive_downloader(FakeDriveOpener([response])).download(
                        f"https://drive.google.com/file/d/{DRIVE_ID}/view", Path(directory) / "source"
                    )
                self.assertIn(message, str(caught.exception))
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cancel_and_size_limit_remove_the_partial_file(self) -> None:
        with TemporaryDirectory() as directory:
            downloader = SocialVideoDownloader(
                max_bytes=4, ffprobe_location="/nonexistent/ffprobe",
                http_opener_factory=lambda jar: FakeDriveOpener(
                    [FakeDriveResponse(b"video-bytes", {**VIDEO_HEADERS, "Content-Length": ""})]
                ),
            )
            with self.assertRaises(SocialVideoDownloadError):
                downloader.download(f"https://drive.google.com/file/d/{DRIVE_ID}/view", Path(directory) / "source")
            opener = FakeDriveOpener([FakeDriveResponse(b"video-bytes", VIDEO_HEADERS)])
            with self.assertRaises(SocialVideoDownloadCancelled):
                drive_downloader(opener).download(
                    f"https://drive.google.com/file/d/{DRIVE_ID}/view", Path(directory) / "source",
                    cancel_requested=lambda: True,
                )
            self.assertEqual(list(Path(directory).iterdir()), [])


# What xhslink.com redirects to without a login (the user's real share link, 2026-11-04).
XHS_LOGIN_REDIRECT = (
    "https://www.xiaohongshu.com/login?redirectPath=http%3A%2F%2Fwww.xiaohongshu.com%2Fdiscovery%2Fitem%2F"
    "69554df60000000021032ccd%3Fapp_platform%3Dios%26share_from_user_hidden%3Dtrue%26xsec_source%3Dapp_share"
    "%26type%3Dnormal%26xsec_token%3DCBtquDr1Z0YgswsLLtWcGSMs4RojEm-1qfpPhwR36XnH4%3D%26xhsshare%3DCopyLink"
)
XHS_NOTE = (
    "https://www.xiaohongshu.com/explore/69554df60000000021032ccd"
    "?xsec_token=CBtquDr1Z0YgswsLLtWcGSMs4RojEm-1qfpPhwR36XnH4%3D&xsec_source=app_share"
)


class XiaohongshuTests(unittest.TestCase):
    def test_short_links_and_note_pages_are_accepted(self) -> None:
        text = "看看这个 http://xhslink.com/o/jxVcmTNr5R 复制后打开【小红书】查看笔记！"
        self.assertEqual(extract_social_video_urls(text), ["http://xhslink.com/o/jxVcmTNr5R"])
        self.assertEqual(social_platform("http://xhslink.com/o/jxVcmTNr5R"), "Xiaohongshu")
        for url in (
            "https://www.xiaohongshu.com/explore/69554df60000000021032ccd?xsec_token=abc",
            "https://www.xiaohongshu.com/discovery/item/69554df60000000021032ccd",
        ):
            self.assertIsNone(social_video_download_issue(url), url)

    def test_profile_and_home_pages_are_rejected(self) -> None:
        for url in ("https://www.xiaohongshu.com/explore", "https://www.xiaohongshu.com/user/profile/5f0c1d"):
            self.assertIsNotNone(social_video_download_issue(url), url)
        self.assertIsNotNone(social_video_download_issue("http://xhslink.com/"))

    def test_login_redirect_yields_the_note_and_its_type(self) -> None:
        self.assertEqual(xiaohongshu_note_url(XHS_LOGIN_REDIRECT), (XHS_NOTE, "normal"))

    def test_short_link_is_resolved_before_yt_dlp_with_browser_cookies(self) -> None:
        with TemporaryDirectory() as directory:
            instances = []

            def factory(options):
                instances.append(FakeYoutubeDL(options))
                return instances[-1]

            def provider(path: Path) -> Path:
                path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
                return path

            seen_urls = []
            original = FakeYoutubeDL.extract_info

            def extract(self, url, *, download):
                seen_urls.append(url)
                return original(self, url, download=download)

            downloader = SocialVideoDownloader(
                ydl_factory=factory,
                redirect_resolver=lambda url: XHS_LOGIN_REDIRECT,
                platform_cookie_providers={"Xiaohongshu": provider},
            )
            with patch.object(FakeYoutubeDL, "extract_info", extract):
                downloader.download("http://xhslink.com/o/jxVcmTNr5R", Path(directory) / "video")

            self.assertEqual(seen_urls, [XHS_NOTE])
            self.assertEqual(instances[0].options["allowed_extractors"], ["xiaohongshu"])
            self.assertIn("cookiefile", instances[0].options)

    def test_failures_explain_login_and_image_posts(self) -> None:
        class NoFormats(FakeYoutubeDL):
            def extract_info(self, url, *, download):
                raise RuntimeError("ERROR: [XiaoHongShu] 69554df6: No video formats found!")

        with TemporaryDirectory() as directory:
            guest = SocialVideoDownloader(ydl_factory=NoFormats, redirect_resolver=lambda url: XHS_LOGIN_REDIRECT)
            with self.assertRaisesRegex(SocialVideoDownloadError, "đăng nhập"):
                guest.download("http://xhslink.com/o/jxVcmTNr5R", Path(directory) / "video")

            def provider(path: Path) -> Path:
                path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
                return path

            logged_in = SocialVideoDownloader(
                ydl_factory=NoFormats, redirect_resolver=lambda url: XHS_LOGIN_REDIRECT,
                platform_cookie_providers={"Xiaohongshu": provider},
            )
            with self.assertRaisesRegex(SocialVideoDownloadError, "bài đăng ảnh"):
                logged_in.download("http://xhslink.com/o/jxVcmTNr5R", Path(directory) / "video")


if __name__ == "__main__":
    unittest.main()
