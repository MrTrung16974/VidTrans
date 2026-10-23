from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.media_probe import probe_media_file
from main import (
    build_voice_track,
    DEFAULT_ORIGINAL_AUDIO_VOLUME,
    DEFAULT_VOICE_MIX_GAIN,
    FFMPEG,
    FFPROBE,
    get_media_duration,
    is_speakable_tts_text,
    mix_audio,
    normalize_subtitle_style,
    wrap_text,
    write_ass,
    write_srt,
    synthesize_tts_segments,
)


@unittest.skipUnless(shutil.which(FFMPEG) and shutil.which(FFPROBE), "ffmpeg/ffprobe required")
class AudioDurationIntegrationTests(unittest.TestCase):
    def test_tts_skips_punctuation_only_cue_before_calling_provider(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vidtrans-empty-tts-") as directory:
            work_dir = Path(directory)
            segments = [{"start": 0.0, "end": 1.0, "text": "…?! --"}]
            paths = synthesize_tts_segments(
                segments,
                work_dir,
                voice_type="female",
                speech_rate=1.0,
            )
            voice_track = build_voice_track(segments, work_dir, video_duration=1.0)
            duration = get_media_duration(voice_track)
        self.assertEqual(paths, [])
        self.assertTrue(segments[0]["skip_tts"])
        self.assertEqual(segments[0]["tts_skip_reason"], "no_speakable_text")
        self.assertAlmostEqual(duration, 1.0, places=2)

    def test_tts_reads_translation_without_pauses_from_source_word_gaps(self) -> None:
        spoken: list[str] = []

        def fake_edge(text, output_path, *_args, **_kwargs) -> None:
            spoken.append(text)
            subprocess.run(
                [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=1", str(output_path)],
                check=True,
            )

        with tempfile.TemporaryDirectory(prefix="vidtrans-tts-text-") as directory:
            segments = [{
                "start": 0.0,
                "end": 3.0,
                "text": "Tôi có thể vỡ nát, nhưng không thể héo mòn",
                # Long pauses between the Chinese words used to turn into commas.
                "words": [
                    {"start": 0.0, "end": 0.4}, {"start": 1.0, "end": 1.4}, {"start": 2.0, "end": 2.4},
                ],
            }]
            with patch("main.tts_edge_with_retry", side_effect=fake_edge):
                synthesize_tts_segments(segments, Path(directory), voice_type="female", speech_rate=1.0)
        self.assertEqual(spoken[0], "Tôi có thể vỡ nát, nhưng không thể héo mòn")

    def test_tts_speakable_check_supports_vietnamese_and_chinese(self) -> None:
        self.assertTrue(is_speakable_tts_text("Được rồi"))
        self.assertTrue(is_speakable_tts_text("有这么完美的女人"))
        self.assertFalse(is_speakable_tts_text("  …?! --  "))

    def test_balanced_default_voice_and_original_audio_levels(self) -> None:
        self.assertEqual(DEFAULT_VOICE_MIX_GAIN, 1.30)
        self.assertEqual(DEFAULT_ORIGINAL_AUDIO_VOLUME, 0.60)

    def test_colored_box_style_is_normalized_for_ass(self) -> None:
        style = normalize_subtitle_style(
            {
                "text_color_css": "#FFEE00",
                "background_color_css": "#102030",
                "background_opacity": 0.8,
                "background_box": True,
            }
        )
        self.assertEqual(style["primary_color"], "&H0000EEFF")
        self.assertEqual(style["back_color"], "&H33302010")
        self.assertEqual(style["border_style"], 3)

    def test_ass_writes_opaque_box_style_from_css_colors(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vidtrans-ass-color-") as directory:
            output = Path(directory) / "sample.ass"
            write_ass(
                [{"start": 0.0, "end": 1.0, "text": "Vietsub có màu"}],
                output,
                {
                    "text_color_css": "#FFFFFF",
                    "background_color_css": "#000000",
                    "background_opacity": 0.82,
                    "background_box": True,
                },
            )
            value = output.read_text(encoding="utf-8")
            self.assertIn(",3,5,0,", value)
            self.assertIn("&H00FFFFFF", value)

    def test_wrap_text_keeps_every_word(self) -> None:
        text = "một hai ba bốn năm sáu bảy tám chín mười mười một mười hai"
        wrapped = wrap_text(text, width=12)
        self.assertEqual(wrapped.replace("\n", " "), text)
        self.assertLessEqual(len(wrapped.splitlines()), 2)

    def test_srt_starts_with_source_cue_when_dub_drifts(self) -> None:
        # Subtitles appear with the source caption (pipeline/subtitle_timing.py)
        # and stay up while a later-scheduled dubbed line is still speaking.
        with tempfile.TemporaryDirectory(prefix="vidtrans-srt-timing-") as directory:
            output = Path(directory) / "sample.srt"
            write_srt(
                [{"start": 1.0, "end": 2.0, "tts_start": 4.0, "tts_end": 6.0, "text": "Xin chào"}],
                output,
            )
            value = output.read_text(encoding="utf-8")
            self.assertIn("00:00:01,000 --> 00:00:06,180", value)

    def test_srt_uses_source_timing_without_tts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vidtrans-srt-timing-") as directory:
            output = Path(directory) / "sample.srt"
            write_srt([{"start": 1.0, "end": 2.0, "text": "Xin chào"}], output)
            value = output.read_text(encoding="utf-8")
            self.assertIn("00:00:01,000 --> 00:00:02,180", value)

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
