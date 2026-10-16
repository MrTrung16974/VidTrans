from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from infrastructure.media_probe import MediaProbeError, probe_media_file


def runner_for(payload):
    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
    return run


class MediaProbeTests(unittest.TestCase):
    def test_parses_video_audio_duration_dimensions_and_fps(self) -> None:
        probe = probe_media_file(
            Path("synthetic.mp4"),
            runner=runner_for(
                {
                    "streams": [
                        {"codec_type": "video", "width": 1920, "height": 1080, "r_frame_rate": "30000/1001"},
                        {"codec_type": "audio"},
                    ],
                    "format": {"duration": "12.5"},
                }
            ),
        )
        self.assertEqual((probe.width, probe.height, probe.duration), (1920, 1080, 12.5))
        self.assertAlmostEqual(probe.fps, 29.97003, places=4)
        self.assertTrue(probe.has_audio)

    def test_defaults_fps_and_handles_video_without_audio(self) -> None:
        probe = probe_media_file(
            Path("synthetic.mp4"),
            runner=runner_for(
                {
                    "streams": [{"codec_type": "video", "width": 640, "height": 360, "duration": "3"}],
                    "format": {},
                }
            ),
        )
        self.assertEqual(probe.fps, 30.0)
        self.assertFalse(probe.has_audio)

    def test_rejects_payload_without_video(self) -> None:
        with self.assertRaises(MediaProbeError):
            probe_media_file(
                Path("synthetic.mp4"),
                runner=runner_for({"streams": [{"codec_type": "audio"}], "format": {"duration": "2"}}),
            )


if __name__ == "__main__":
    unittest.main()
