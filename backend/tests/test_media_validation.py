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
        self.assertIn("Video nguồn", message)
        self.assertLess(len(message), 150)

    def test_truncates_unknown_internal_error(self) -> None:
        message = public_error_message("x" * 1000, max_length=80)
        self.assertEqual(len(message), 80)
        self.assertTrue(message.endswith("…"))


if __name__ == "__main__":
    unittest.main()
