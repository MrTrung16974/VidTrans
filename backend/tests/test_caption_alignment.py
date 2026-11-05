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

    def test_word_stretched_over_a_pause_follows_its_spoken_tail(self) -> None:
        # Timings from a real job: Whisper stretched "肯" back over the pause before
        # "肯定不是他", so most of its span sat in the previous caption.
        segments = [{
            "start": 108.2,
            "end": 110.5,
            "text": "会不会是他肯定不是他哎",
            "words": [
                _word("会", 108.2, 108.5), _word("不会", 108.5, 108.64), _word("是", 108.64, 108.8),
                _word("他", 108.8, 108.94), _word("肯", 108.94, 109.58), _word("定", 109.58, 109.7),
                _word("不是", 109.7, 109.9), _word("他", 109.9, 110.2), _word("哎", 110.2, 110.5),
            ],
        }]
        # The real job measured no speech lead; pin it so the lead search cannot mask the stretch.
        aligned = align_words_to_captions(
            segments, [(108.33, 109.13), (109.4, 110.6)], CaptionAlignmentConfig(max_speech_lead=0.0)
        )

        self.assertEqual([cue["text"] for cue in aligned], ["会不会是他", "肯定不是他哎"])

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


class FalseCaptionTests(unittest.TestCase):
    def test_leftover_words_between_captions_stay_separate_and_in_order(self) -> None:
        # Job 8850e74a: "xuân," "đi" "ngày" fell between captions and became one cue
        # spanning 8.3-13.8 s, which later split into cues out of order.
        words = [_word("Quân", 7.0, 7.7), _word("ơi,", 7.7, 8.0), _word("xuân,", 8.3, 8.56),
                 _word("vẫn", 8.9, 9.2), _word("buồn", 9.2, 9.7), _word("đi", 12.68, 12.9),
                 _word("xíu", 13.2, 13.6)]
        segment = {"start": 7.0, "end": 13.6, "text": "Quân ơi, xuân, vẫn buồn đi xíu", "words": words}
        cues = align_words_to_captions([segment], [(7.0, 8.0), (8.83, 9.77), (13.13, 13.7)],
                                       CaptionAlignmentConfig(min_word_coverage=0.5, max_speech_lead=0.0))
        cues = segment_subtitle_cues(cues)
        starts = [cue["start"] for cue in cues]
        self.assertEqual(starts, sorted(starts))
        self.assertIn("xuân,", [cue["text"] for cue in cues])
        self.assertIn("đi", [cue["text"] for cue in cues])

    def test_caption_band_without_text_keeps_whisper_timing(self) -> None:
        from pathlib import Path
        from unittest.mock import patch

        from pipeline.caption_alignment import align_speech_to_burned_captions

        segments = [{"start": 0.0, "end": 2.0, "text": "Kìa mùa xuân", "words": [_word("Kìa", 0.0, 0.5)]}]
        checked = []
        with patch("pipeline.caption_alignment.detect_caption_layout", return_value=([(0.0, 2.0)], None)), \
                patch("pipeline.caption_alignment.caption_check_frames", return_value=[Path("band.png")]):
            result = align_speech_to_burned_captions(
                Path("v.mp4"), segments, ffmpeg="ffmpeg", frame_size=(1080, 1920),
                has_caption_text=lambda frames: checked.append(list(frames)) or False,
            )
        self.assertIs(result, segments)
        self.assertEqual(checked, [[Path("band.png")]])


    def test_aligned_cues_carry_caption_band_position(self) -> None:
        from pathlib import Path
        from unittest.mock import patch

        from pipeline.caption_alignment import align_speech_to_burned_captions
        from pipeline.subtitle_layout import apply_subtitle_layout

        band = {"x": 0.2, "y": 0.615, "width": 0.6, "height": 0.033}
        segments = [{"start": 0.0, "end": 2.0, "text": "Kìa mùa xuân",
                     "words": [_word("Kìa", 0.1, 0.5), _word("mùa", 0.6, 1.0), _word("xuân", 1.1, 1.6)]}]
        with patch("pipeline.caption_alignment.detect_caption_layout", return_value=([(0.0, 2.0)], band)):
            result = align_speech_to_burned_captions(Path("v.mp4"), segments, ffmpeg="ffmpeg", frame_size=(1080, 1920))
        self.assertTrue(result)
        self.assertTrue(all(cue["source_bbox"] == band for cue in result))

        laid = apply_subtitle_layout(result, video_width=1080, video_height=1920,
                                     style={"placement_mode": "replace_original"})
        layout = laid[0]["subtitle_layout"]
        self.assertEqual(layout["placement_mode"], "replace_original")
        self.assertAlmostEqual(layout["y"], (0.615 + 0.033 / 2) * 1920, delta=1)
        self.assertTrue(layout["mask_original"])

if __name__ == "__main__":
    unittest.main()
