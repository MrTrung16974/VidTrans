import unittest
from unittest.mock import patch

from pipeline.video_encoder import VideoEncoder


class VideoEncoderTests(unittest.TestCase):
    def pick(self, env, cpus=4, nvenc=False):
        with patch.dict("os.environ", env, clear=True):
            return VideoEncoder.from_env("ffmpeg", cpus, has_nvenc=lambda ffmpeg: nvenc)

    def test_auto_follows_the_hardware(self):
        self.assertEqual(self.pick({}, cpus=4).args(23), ["-c:v", "libx264", "-preset", "superfast", "-crf", "23"])
        self.assertEqual(self.pick({}, cpus=16).preset, "veryfast")
        gpu = self.pick({}, nvenc=True)
        self.assertEqual(gpu.codec, "h264_nvenc")
        self.assertIn("-cq", gpu.args(23))

    def test_overrides(self):
        self.assertEqual(self.pick({"VIDTRANS_RENDER_ENCODER": "x264"}, nvenc=True).codec, "libx264")
        self.assertEqual(self.pick({"VIDTRANS_RENDER_PRESET": "fast"}).preset, "fast")
        for env in ({"VIDTRANS_RENDER_PRESET": "quick"}, {"VIDTRANS_RENDER_ENCODER": "amf"}):
            with self.assertRaises(ValueError):
                self.pick(env)


if __name__ == "__main__":
    unittest.main()
