import unittest

from pipeline.audio_scene import (
    SceneWindow,
    audio_type,
    has_singing_or_music_only,
    label_segments,
    music_only_regions,
    music_under_speech,
    scene_scores,
)


def window(start: float, speech: float, singing: float, music: float, end: float | None = None) -> SceneWindow:
    return SceneWindow(start, start + 10.0 if end is None else end, speech, singing, music)


# Scores measured on a Douyin clip: film dialogue over a score, then a sung verse.
DIALOGUE_THEN_SONG = [
    window(0, 0.78, 0.04, 0.79),
    window(5, 0.76, 0.07, 0.80),
    window(10, 0.74, 0.13, 0.82),
    window(15, 0.05, 0.25, 0.75, 25.0),
    window(20, 0.02, 0.08, 0.88, 27.5),
    window(25, 0.03, 0.07, 0.71, 27.5),
]


class AudioSceneTests(unittest.TestCase):
    def test_dialogue_over_music_is_speech_and_words_in_a_song_are_singing(self) -> None:
        self.assertEqual(audio_type(scene_scores(DIALOGUE_THEN_SONG, 1.0, 3.0)), "speech")
        self.assertEqual(audio_type(scene_scores(DIALOGUE_THEN_SONG, 22.0, 24.0)), "singing")
        self.assertIsNone(audio_type(scene_scores([window(0, 0.05, 0.02, 0.1)], 1.0, 2.0)))

    def test_only_the_song_is_muted_for_whisper(self) -> None:
        regions = music_only_regions(DIALOGUE_THEN_SONG)
        self.assertEqual(len(regions), 1)
        start, end = regions[0]
        self.assertGreaterEqual(start, 15.0)
        self.assertEqual(end, 27.5)

    def test_short_or_speech_only_clips_mute_nothing(self) -> None:
        self.assertEqual(music_only_regions([window(0, 0.9, 0.0, 0.2, 6.0)]), [])
        self.assertEqual(music_only_regions([window(0, 0.9, 0.0, 0.9), window(5, 0.2, 0.0, 0.9, 12.0)]), [])

    def test_music_share_and_song_detection(self) -> None:
        self.assertEqual(music_under_speech(DIALOGUE_THEN_SONG), 1.0)
        self.assertTrue(has_singing_or_music_only(DIALOGUE_THEN_SONG))
        self.assertFalse(has_singing_or_music_only([window(0, 0.9, 0.0, 0.2)]))

    def test_cues_are_labelled(self) -> None:
        segments = [{"start": 0.0, "end": 2.0}, {"start": 21.0, "end": 23.0}]
        counts = label_segments(segments, DIALOGUE_THEN_SONG)
        self.assertEqual(counts, {"speech": 1, "singing": 1})
        self.assertEqual(segments[1]["audio_type"], "singing")
        self.assertIn("music", segments[0]["audio_scene"])
