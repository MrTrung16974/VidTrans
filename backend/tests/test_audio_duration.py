from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from infrastructure.media_probe import probe_media_file
from main import FFMPEG, FFPROBE, get_media_duration, mix_audio


@unittest.skipUnless(shutil.which(FFMPEG) and shutil.which(FFPROBE), "ffmpeg/ffprobe required")
class AudioDurationIntegrationTests(unittest.TestCase):
    def test_generated_only_mix_is_padded_to_source_video_duration(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vidtrans-audio-duration-") as directory:
            root = Path(directory)
            source = root / "source.mp4"
            voice = root / "voice.wav"
            subprocess.run(
                [
                    FFMPEG, "-y", "-f", "lavfi", "-i",
                    "color=c=black:s=160x90:r=10:d=2", "-f", "lavfi", "-i",
                    "anullsrc=r=24000:cl=mono:d=2", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", str(source),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    FFMPEG, "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=24000:duration=0.5", str(voice),
                ],
                check=True,
                capture_output=True,
            )
            source_probe = probe_media_file(source, ffprobe=FFPROBE)
            output = mix_audio(
                mode=2,
                video_path=source,
                work_dir=root,
                voice_track=voice,
                background_music=None,
                keep_original_audio=False,
                original_audio_volume=0.2,
                music_volume=0.15,
                source_probe=source_probe,
            )
            self.assertIsNotNone(output)
            self.assertAlmostEqual(get_media_duration(Path(output)), source_probe.duration, delta=0.05)


if __name__ == "__main__":
    unittest.main()
