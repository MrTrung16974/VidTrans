import unittest

from pipeline.subtitle_segmentation import SubtitleSegmentationOptions, segment_subtitle_cues


class SubtitleSegmentationTests(unittest.TestCase):
    def test_splits_on_sentence_and_pause_using_word_timestamps(self) -> None:
        result = segment_subtitle_cues(
            [
                {
                    "start": 1.0,
                    "end": 5.0,
                    "text": "你好。欢迎回来今天继续",
                    "words": [
                        {"word": "你", "start": 1.0, "end": 1.2},
                        {"word": "好。", "start": 1.2, "end": 1.5},
                        {"word": "欢迎", "start": 2.3, "end": 2.7},
                        {"word": "回来", "start": 2.7, "end": 3.1},
                        {"word": "今天", "start": 4.0, "end": 4.3},
                        {"word": "继续", "start": 4.3, "end": 4.8},
                    ],
                }
            ]
        )
        self.assertEqual([cue["text"] for cue in result], ["你好。", "欢迎回来", "今天继续"])
        self.assertEqual([(cue["start"], cue["end"]) for cue in result], [(1.0, 1.5), (2.3, 3.1), (4.0, 4.8)])
        self.assertEqual([cue["cue_id"] for cue in result], ["cue-000000-000", "cue-000000-001", "cue-000000-002"])

    def test_flushes_final_group_at_end_of_video(self) -> None:
        result = segment_subtitle_cues(
            [
                {
                    "start": 8.0,
                    "end": 10.0,
                    "text": "第一句。最后一句",
                    "words": [
                        {"word": "第一句。", "start": 8.0, "end": 8.8},
                        {"word": "最后", "start": 9.1, "end": 9.5},
                        {"word": "一句", "start": 9.5, "end": 9.9},
                    ],
                }
            ]
        )
        self.assertEqual(result[-1]["text"], "最后一句")
        self.assertEqual(result[-1]["end"], 9.9)

    def test_preserves_untimed_cue_without_even_timestamp_split(self) -> None:
        source = {"start": 0.0, "end": 8.0, "text": "没有逐词时间的很长字幕", "words": []}
        result = segment_subtitle_cues([source], options=SubtitleSegmentationOptions(max_chars=8, preferred_chars=6))
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["start"], result[0]["end"]), (0.0, 8.0))

    def test_splits_continuous_speech_at_max_timed_duration(self) -> None:
        result = segment_subtitle_cues(
            [
                {
                    "start": 0.0,
                    "end": 6.0,
                    "text": "连续说话没有标点也需要及时换字幕",
                    "words": [
                        {"word": "连续", "start": 0.0, "end": 0.8},
                        {"word": "说话", "start": 0.8, "end": 1.6},
                        {"word": "没有", "start": 1.6, "end": 2.4},
                        {"word": "标点", "start": 2.4, "end": 3.2},
                        {"word": "也需要", "start": 3.2, "end": 4.2},
                        {"word": "及时", "start": 4.2, "end": 5.0},
                        {"word": "换字幕", "start": 5.0, "end": 5.8},
                    ],
                }
            ],
            options=SubtitleSegmentationOptions(
                preferred_chars=18,
                max_chars=30,
                max_duration_seconds=3.0,
            ),
        )
        self.assertGreaterEqual(len(result), 2)
        self.assertTrue(all(cue["end"] - cue["start"] <= 3.3 for cue in result))


if __name__ == "__main__":
    unittest.main()
