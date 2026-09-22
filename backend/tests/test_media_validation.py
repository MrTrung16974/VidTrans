from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.media_validation import InvalidVideoError, public_error_message, validate_video_file


class MediaValidationTests(unittest.TestCase):
    def test_rejects_empty_file_before_running_ffprobe(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "empty.mp4"
            path.touch()
            calls = 0

            def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
                nonlocal calls
                calls += 1
                return subprocess.CompletedProcess([], 0, "{}", "")

            with self.assertRaisesRegex(InvalidVideoError, "rỗng"):
                validate_video_file(path, runner=runner)
            self.assertEqual(calls, 0)

    def test_accepts_video_stream_with_dimensions_and_duration(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "valid.mp4"
            path.write_bytes(b"video")
            payload = {"streams": [{"width": 1080, "height": 1920}], "format": {"duration": "12.5"}}

            def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess([], 0, json.dumps(payload), "")

            probe = validate_video_file(path, runner=runner)
            self.assertEqual((probe.width, probe.height, probe.duration), (1080, 1920, 12.5))

    def test_rejects_ffprobe_failure_with_short_vietnamese_error(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "broken.mp4"
            path.write_bytes(b"broken")

            def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
                raise subprocess.CalledProcessError(1, ["ffprobe"], stderr="moov atom not found")

            with self.assertRaisesRegex(InvalidVideoError, "bị hỏng"):
                validate_video_file(path, runner=runner)

    def test_collapses_verbose_ffmpeg_error_for_dashboard(self) -> None:
        raw = "Failed to load audio: ffmpeg version 7.1 " + ("configuration details " * 100)
        message = public_error_message(raw)
        self.assertIn("âm thanh", message)
        self.assertLess(len(message), 200)

    def test_audio_error_is_not_mislabeled_as_corrupt_video(self):
        self.assertNotIn("Video nguồn bị rỗng", public_error_message("Failed to load audio: conversion failed"))
        self.assertIn("quyền", public_error_message("Failed to load audio: Permission denied"))
        self.assertIn("dung lượng", public_error_message("Failed to load audio: No space left on device"))
        self.assertIn("Không tìm thấy", public_error_message("Error opening input file: No such file or directory"))

    def test_unavailable_stream_duration_uses_container_duration(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "valid.webm"
            path.write_bytes(b"video")
            for value in ("N/A", "nan", "inf", "0"):
                payload = {"streams": [{"width": 640, "height": 480, "duration": value}],
                           "format": {"duration": "5.0"}}
                probe = validate_video_file(path, runner=lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(payload), ""))
                self.assertEqual(probe.duration, 5.0)

    def test_truncates_unknown_internal_error(self) -> None:
        message = public_error_message("x" * 1000, max_length=80)
        self.assertEqual(len(message), 80)
        self.assertTrue(message.endswith("…"))


if __name__ == "__main__":
    unittest.main()
