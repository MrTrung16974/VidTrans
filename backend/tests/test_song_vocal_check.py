from __future__ import annotations

import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import main
from infrastructure.media_probe import MediaProbe
from pipeline.vocal_separation import voiced_seconds

RATE = 16000
VIDEO = Path("clip.mp4")
VOCALS = Path("work/vocals.wav")


def tone(seconds: float, level: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return (level * np.sqrt(2) * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


class VoicedSecondsTests(unittest.TestCase):
    def test_counts_only_frames_where_the_stem_keeps_the_voice(self) -> None:
        mix = tone(4.0, 0.2)
        vocals = np.concatenate([tone(2.0, 0.1), tone(2.0, 0.01)])  # voice, then bleed

        self.assertAlmostEqual(voiced_seconds(vocals, mix, [(0.0, 4.0)]), 2.0, places=1)
        self.assertAlmostEqual(voiced_seconds(vocals, mix, [(2.0, 4.0)]), 0.0, places=1)

    def test_silent_stem_is_not_a_voice(self) -> None:
        self.assertEqual(voiced_seconds(np.zeros(RATE * 2, np.float32), np.zeros(RATE * 2, np.float32), [(0, 2)]), 0)


class SongVocalCheckTests(unittest.TestCase):
    """A 58 s song the classifier hears as music: 0-46.5 s and 51-58 s muted."""

    SCENES = [
        main.SceneWindow(float(start), float(start + 10), speech=0.01, singing=0.03, music=0.9)
        for start in range(0, 45, 5)
    ] + [main.SceneWindow(45.0, 55.0, speech=0.4, singing=0.03, music=0.9),
         main.SceneWindow(50.0, 58.0, speech=0.01, singing=0.03, music=0.9)]

    def setUp(self) -> None:
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(main, "update_job", lambda job_id, **fields: None))
        stack.enter_context(patch.object(main, "ensure_job_active", lambda job_id: None))
        stack.enter_context(patch.object(main, "record_job_stage_measurement", lambda job_id, measurement: None))
        stack.enter_context(patch.object(main, "AUDIO_SCENE_ENABLED", True))
        stack.enter_context(patch.object(main, "VOCAL_SEPARATION_MODE", "off"))
        stack.enter_context(patch.object(main, "SONG_VOCAL_CHECK_ENABLED", True))
        stack.enter_context(patch.object(
            main, "AUDIO_SCENES", SimpleNamespace(classify=lambda samples, check_active: list(self.SCENES))
        ))
        self.separated: list[Path] = []
        stack.enter_context(patch.object(
            main, "VOCAL_SEPARATOR",
            SimpleNamespace(separate=lambda media, output: self.separated.append(media) or VOCALS),
        ))
        self.vocal_level = 0.1
        stack.enter_context(patch(
            "faster_whisper.audio.decode_audio",
            lambda path, sampling_rate: tone(58.0, self.vocal_level if str(path) == str(VOCALS) else 0.2),
        ))

    def prepare(self) -> tuple[Path, list[tuple[float, float]]]:
        return main.prepare_speech_audio("job-song", VIDEO, Path("work"), 58.0, [])

    def test_sung_lyrics_are_heard_on_the_vocal_stem(self) -> None:
        media, muted = self.prepare()

        self.assertEqual(self.separated, [VIDEO])
        self.assertEqual(media, VOCALS)
        self.assertEqual(muted, [])

    def test_instrumental_keeps_its_music_muted_on_the_original_audio(self) -> None:
        self.vocal_level = 0.005

        media, muted = self.prepare()

        self.assertEqual(media, VIDEO)
        self.assertTrue(muted)

    def test_check_can_be_turned_off(self) -> None:
        with patch.object(main, "SONG_VOCAL_CHECK_ENABLED", False):
            media, muted = self.prepare()

        self.assertEqual(self.separated, [])
        self.assertEqual(media, VIDEO)
        self.assertTrue(muted)


class MusicOnlyFailureMessageTests(unittest.TestCase):
    def test_song_with_no_lyrics_says_so(self) -> None:
        def prepare(job_id, video_path, work_dir, duration, scenes):
            scenes.extend(SongVocalCheckTests.SCENES)
            return video_path, []

        with ExitStack() as stack:
            stack.enter_context(patch.object(main, "update_job", lambda job_id, **fields: None))
            stack.enter_context(patch.object(main, "ensure_job_active", lambda job_id: None))
            stack.enter_context(patch.object(main, "record_job_stage_measurement", lambda job_id, measurement: None))
            stack.enter_context(patch.object(main, "SETTINGS", SimpleNamespace(ocr_backend="off", ocr_model="m")))
            stack.enter_context(patch.object(main, "prepare_speech_audio", prepare))
            stack.enter_context(patch.object(main, "transcribe_video", lambda *args, **kwargs: ([], "en", 0.5)))
            with self.assertRaisesRegex(RuntimeError, "chủ yếu là nhạc/tiếng hát"):
                main.recognize_video_source(
                    job_id="job-song",
                    video_path=VIDEO,
                    work_dir=Path("work"),
                    source_probe=MediaProbe(duration=58.0, width=0, height=0, fps=30.0, has_audio=True),
                    subtitle_source="speech",
                    source_language="auto",
                    whisper_model="medium",
                    ocr_sample_fps=5.0,
                    ocr_roi_top=0.68,
                    ocr_roi_bottom=0.96,
                )


if __name__ == "__main__":
    unittest.main()
