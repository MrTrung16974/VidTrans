import unittest

import numpy as np

from pipeline.caption_alignment import (
    CaptionAlignmentConfig,
    align_words_to_captions,
    caption_intervals,
    locate_caption_band,
)
from pipeline.subtitle_segmentation import SubtitleSegmentationOptions, segment_subtitle_cues
from pipeline.subtitle_timing import subtitle_render_window


def _word(text: str, start: float, end: float) -> dict:
    return {"word": text, "start": start, "end": end, "probability": 0.9}


class CaptionBandTests(unittest.TestCase):
    def test_finds_rows_and_columns_that_light_up_during_speech(self) -> None:
        masks = np.zeros((100, 20, 40), dtype=bool)
        rng = np.random.default_rng(1)
        masks |= rng.random(masks.shape) < 0.02  # scenery noise everywhere
        speech_frames = range(20, 80)
        for frame in speech_frames:
            masks[frame, 14:17, 10:30] = rng.random((3, 20)) < 0.6
        times = [index / 5 for index in range(100)]
        segments = [{"start": 4.0, "end": 16.0}]

        band = locate_caption_band(masks, times, segments)

        self.assertIsNotNone(band)
        top, bottom, left, right = band
        self.assertAlmostEqual(top, 14 / 20)
        self.assertAlmostEqual(bottom, 17 / 20)
        self.assertLessEqual(left, 10 / 40)
        self.assertGreaterEqual(right, 30 / 40)

    def test_reports_no_band_when_nothing_follows_speech(self) -> None:
        masks = np.random.default_rng(2).random((100, 20, 40)) < 0.01
        times = [index / 5 for index in range(100)]
        self.assertIsNone(locate_caption_band(masks, times, [{"start": 4.0, "end": 16.0}]))


class CaptionIntervalTests(unittest.TestCase):
    def test_splits_on_caption_change_and_tolerates_short_dropout(self) -> None:
        config = CaptionAlignmentConfig()
        rate = config.frame_rate
        density = [0.0] * 300
        changes = [1.0] * 300
        for frame in range(30, 90):
            density[frame] = 0.3
        density[45] = 0.0  # one dropped frame inside the first caption
        changes[60] = 0.1  # a new caption replaces the first one
        times = [index / rate for index in range(300)]

        intervals = caption_intervals(density, changes, times, [{"start": 1.0, "end": 3.0}], config)

        self.assertEqual(len(intervals), 2)
        self.assertAlmostEqual(intervals[0][0], 30 / rate)
        self.assertAlmostEqual(intervals[0][1], 60 / rate)
        self.assertAlmostEqual(intervals[1][0], 60 / rate)
        self.assertAlmostEqual(intervals[1][1], 90 / rate)


class WordAlignmentTests(unittest.TestCase):
    def test_regroups_words_into_caption_windows_with_speech_lead(self) -> None:
        # Whisper split the sentence in the wrong place; the voice leads the
        # captions by 0.2 s.
        segments = [
            {
                "start": 0.8,
                "end": 2.1,
                "text": "可惜我们",
                "words": [_word("可", 0.8, 1.0), _word("惜", 1.0, 1.3), _word("我们", 1.8, 2.1)],
            },
            {
                "start": 2.1,
                "end": 3.4,
                "text": "两年没见了",
                "words": [_word("两年", 2.1, 2.6), _word("没", 2.6, 2.9), _word("见了", 2.9, 3.4)],
            },
        ]
        intervals = [(1.0, 1.9), (1.9, 3.6)]

        aligned = align_words_to_captions(segments, intervals)

        self.assertEqual([cue["text"] for cue in aligned], ["可惜", "我们两年没见了"])
        self.assertEqual([(cue["start"], cue["end"]) for cue in aligned], intervals)
        self.assertTrue(all(cue["timing_source"] == "caption" for cue in aligned))

    def test_keeps_whisper_timing_for_speech_without_caption(self) -> None:
        segments = [
            {"start": 1.0, "end": 2.0, "text": "你好", "words": [_word("你好", 1.0, 2.0)]},
            {"start": 2.0, "end": 3.0, "text": "朋友", "words": [_word("朋友", 2.0, 3.0)]},
            {"start": 3.0, "end": 4.0, "text": "再见", "words": [_word("再见", 3.0, 4.0)]},
            {"start": 8.0, "end": 9.0, "text": "旁白", "words": [_word("旁白", 8.0, 9.0)]},
        ]
        aligned = align_words_to_captions(segments, [(1.0, 2.0), (2.0, 3.0), (3.0, 4.0)])

        self.assertEqual(aligned[-1]["text"], "旁白")
        self.assertEqual((aligned[-1]["start"], aligned[-1]["end"]), (8.0, 9.0))
        self.assertNotIn("timing_source", aligned[-1])

    def test_returns_none_when_words_do_not_fit_any_caption(self) -> None:
        segments = [
            {"start": 1.0, "end": 2.0, "text": "你好", "words": [_word("你好", 1.0, 2.0)]},
            {"start": 5.0, "end": 6.0, "text": "朋友", "words": [_word("朋友", 5.0, 6.0)]},
        ]
        self.assertIsNone(align_words_to_captions(segments, [(10.0, 11.0)]))


class CaptionTimedCueTests(unittest.TestCase):
    def test_segmentation_does_not_split_caption_aligned_cue(self) -> None:
        cue = {
            "start": 0.0,
            "end": 5.0,
            "text": "一二三四五六七八九十一二三四五六七八九十一二",
            "words": [_word(char, index * 0.2, index * 0.2 + 0.2) for index, char in enumerate("一二三四五六七八九十一二三四五六七八九十一二")],
            "timing_source": "caption",
        }
        output = segment_subtitle_cues([cue], options=SubtitleSegmentationOptions())
        self.assertEqual(len(output), 1)

    def test_caption_aligned_cue_ends_with_the_caption(self) -> None:
        segments = [
            {"start": 1.0, "end": 2.0, "tts_end": 2.6, "text": "Xin chào", "timing_source": "caption"},
        ]
        self.assertEqual(subtitle_render_window(segments, 0), (1.0, 2.0))


if __name__ == "__main__":
    unittest.main()
