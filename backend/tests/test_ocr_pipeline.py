import unittest
import threading

from pipeline.ocr import (
    FrameObservation,
    OCRConfig,
    OCRLine,
    PaddleOCRReader,
    annotate_ocr_segments_with_asr,
    normalize_zh_text,
    observations_to_cues,
    select_subtitle_text,
    select_subtitle_geometry,
    text_similarity,
)


class _ArrayLike:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return self._values

    def __bool__(self):
        raise AssertionError("array-like OCR results must not be evaluated as booleans")


class _FakePaddleV3:
    def predict(self, _image_path):
        return [
            {
                "res": {
                    "rec_texts": ["我愿意把自己变成什么样子"],
                    "rec_scores": _ArrayLike([0.97]),
                    "rec_boxes": _ArrayLike([[10, 20, 200, 50]]),
                }
            }
        ]


class OCRTextTests(unittest.TestCase):
    def test_normalize_preserves_chinese_variant_and_removes_spacing(self) -> None:
        self.assertEqual(normalize_zh_text(" 我 愿意　變成什麼樣子 "), "我愿意變成什麼樣子")

    def test_similarity_tolerates_one_bad_character(self) -> None:
        self.assertGreater(text_similarity("我愿意把自己变成什么样子", "我愿意把自已变成什么样子"), 0.9)

    def test_selects_only_chinese_lines_in_visual_order(self) -> None:
        text, confidence = select_subtitle_text(
            [
                OCRLine("VidTrans", 0.99, (0, 0, 100, 20)),
                OCRLine("什么样子", 0.92, (300, 80, 500, 120)),
                OCRLine("我愿意把自己变成", 0.96, (100, 40, 500, 75)),
            ],
            min_confidence=0.55,
        )
        self.assertEqual(text, "我愿意把自己变成\n什么样子")
        self.assertGreater(confidence, 0.92)

    def test_paddle_v3_adapter_accepts_array_results(self) -> None:
        reader = PaddleOCRReader.__new__(PaddleOCRReader)
        reader._engine = _FakePaddleV3()
        reader._api_version = 3
        reader._inference_lock = threading.Lock()
        lines = reader.read(None)
        self.assertEqual(lines, [OCRLine("我愿意把自己变成什么样子", 0.97, (10, 20, 200, 50))])

    def test_normalizes_union_bbox_for_selected_chinese_lines(self) -> None:
        text, confidence, bbox = select_subtitle_geometry(
            [
                OCRLine("第一行", 0.95, (100, 40, 500, 80)),
                OCRLine("第二行", 0.93, (140, 90, 480, 130)),
            ],
            0.55,
            frame_width=1000,
            frame_height=200,
        )
        self.assertEqual(text, "第一行\n第二行")
        self.assertGreater(confidence, 0.93)
        self.assertEqual(bbox, (0.1, 0.2, 0.5, 0.65))

    def test_asr_evidence_flags_low_agreement_without_overwriting_ocr(self) -> None:
        ocr = [{
            "start": 1.0,
            "end": 2.0,
            "text": "我愿意把自己变成什么样子",
            "ocr_confidence": 0.96,
        }]
        result = annotate_ocr_segments_with_asr(
            ocr,
            [{"start": 1.1, "end": 1.9, "text": "天气非常好"}],
        )
        self.assertEqual(result[0]["text"], "我愿意把自己变成什么样子")
        self.assertTrue(result[0]["needs_review"])
        self.assertLess(result[0]["asr_similarity"], 0.45)


class TemporalConsensusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = OCRConfig(
            sample_fps=5,
            min_confidence=0.55,
            text_similarity=0.8,
            blank_tolerance_frames=1,
            min_stable_frames=2,
        )

    def test_uses_repeated_variant_as_consensus(self) -> None:
        observations = [
            FrameObservation(0.0, "我愿意把自己变成什么样子", 0.96),
            FrameObservation(0.2, "我愿意把自己变成什么样子", 0.95),
            FrameObservation(0.4, "我愿意把自已变成什么样子", 0.91),
            FrameObservation(0.6, "我愿意把自己变成什么样子", 0.97),
        ]
        cues = observations_to_cues(observations, self.config)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "我愿意把自己变成什么样子")
        self.assertEqual(cues[0].samples, 4)
        self.assertAlmostEqual(cues[0].start, 0.0)
        self.assertAlmostEqual(cues[0].end, 0.7)

    def test_preserves_stable_median_bbox_in_video_coordinates(self) -> None:
        observations = [
            FrameObservation(0.0, "字幕", 0.96, (0.2, 0.4, 0.8, 0.6)),
            FrameObservation(0.2, "字幕", 0.95, (0.21, 0.41, 0.79, 0.61)),
        ]
        cues = observations_to_cues(observations, self.config)
        segment = cues[0].as_segment()
        self.assertEqual(segment["position_source"], "ocr")
        self.assertAlmostEqual(segment["source_bbox"]["x"], 0.205)
        self.assertAlmostEqual(segment["source_bbox"]["y"], 0.68 + 0.405 * 0.28)

    def test_tolerates_one_blank_frame_but_splits_on_text_change(self) -> None:
        observations = [
            FrameObservation(0.0, "没有人可以左右我的思想", 0.95),
            FrameObservation(0.2, "没有人可以左右我的思想", 0.96),
            FrameObservation(0.4, "", 0.0),
            FrameObservation(0.6, "没有人可以左右我的思想", 0.94),
            FrameObservation(0.8, "我愿意把自己变成什么样子", 0.95),
            FrameObservation(1.0, "我愿意把自己变成什么样子", 0.96),
        ]
        cues = observations_to_cues(observations, self.config)
        self.assertEqual([cue.text for cue in cues], [
            "没有人可以左右我的思想",
            "我愿意把自己变成什么样子",
        ])
        self.assertLessEqual(cues[0].end, cues[1].start)

    def test_discards_single_frame_false_positive(self) -> None:
        cues = observations_to_cues(
            [FrameObservation(0.0, "错误", 0.99)],
            self.config,
        )
        self.assertEqual(cues, [])

    def test_reconnects_same_cue_after_one_severe_ocr_error(self) -> None:
        observations = [
            FrameObservation(0.0, "我愿意把自己变成什么样子", 0.96),
            FrameObservation(0.2, "我愿意把自己变成什么样子", 0.95),
            FrameObservation(0.4, "完全错误的字幕", 0.99),
            FrameObservation(0.6, "我愿意把自己变成什么样子", 0.94),
            FrameObservation(0.8, "我愿意把自己变成什么样子", 0.96),
        ]
        cues = observations_to_cues(observations, self.config)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].samples, 4)


if __name__ == "__main__":
    unittest.main()
