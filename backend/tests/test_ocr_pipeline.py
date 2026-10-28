import unittest
import threading
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from pipeline.ocr import (
    FrameObservation,
    OCRConfig,
    OCRLine,
    IsolatedPaddleOCRReader,
    is_watermark,
    PaddleOCRReader,
    TesseractOCRReader,
    annotate_ocr_segments_with_asr,
    merge_ocr_and_asr_segments,
    merge_independent_ocr_asr_segments,
    normalize_zh_text,
    observations_to_cues,
    refine_cue_boundaries,
    scan_subtitle_frames,
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

    def test_tesseract_adapter_groups_chinese_words_into_one_line(self) -> None:
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t20\t40\t20\t96\t这个世界\n"
            "5\t1\t1\t1\t1\t2\t52\t20\t35\t20\t94\t怎么会\n"
        )
        completed = type("Completed", (), {"returncode": 0, "stdout": tsv, "stderr": ""})()
        with patch("pipeline.ocr.shutil.which", return_value="/usr/bin/tesseract"), patch(
            "pipeline.ocr.subprocess.run", return_value=completed
        ):
            lines = TesseractOCRReader().read(Path("frame.jpg"))
        self.assertEqual(lines[0].text, "这个世界怎么会")
        self.assertAlmostEqual(lines[0].confidence, 0.95, places=2)
        self.assertEqual(lines[0].bbox, (10.0, 20.0, 87.0, 40.0))

    def test_tesseract_adapter_maps_batch_pages_to_input_frames(self) -> None:
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t20\t40\t20\t96\t第一句\n"
            "5\t3\t1\t1\t1\t1\t12\t22\t42\t22\t94\t第三句\n"
        )
        completed = type("Completed", (), {"returncode": 0, "stdout": tsv, "stderr": ""})()
        with tempfile.TemporaryDirectory() as directory, patch(
            "pipeline.ocr.shutil.which", return_value="/usr/bin/tesseract"
        ), patch("pipeline.ocr.subprocess.run", return_value=completed) as run:
            paths = [Path(directory) / f"frame-{index}.jpg" for index in range(3)]
            results = TesseractOCRReader().read_many(paths)

        self.assertEqual(
            [[line.text for line in page] for page in results],
            [["第一句"], [], ["第三句"]],
        )
        self.assertEqual(run.call_count, 1)
        self.assertTrue(str(run.call_args.args[0][1]).endswith(".txt"))

    def test_scan_reuses_ocr_for_visually_identical_frames(self) -> None:
        class CountingReader:
            def __init__(self) -> None:
                self.calls = 0

            def read(self, _image_path):
                self.calls += 1
                return [OCRLine("相同字幕", 0.96, (5, 5, 40, 20))]

        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(3):
                path = Path(directory) / f"frame-{index}.jpg"
                Image.new("RGB", (64, 32), "white").save(path)
                paths.append(path)
            reader = CountingReader()
            observations = scan_subtitle_frames(
                paths,
                reader=reader,
                config=OCRConfig(sample_fps=3.0),
            )

        self.assertEqual(reader.calls, 1)
        self.assertEqual([item.text for item in observations], ["相同字幕"] * 3)
        self.assertEqual([item.timestamp for item in observations], [0.0, 1 / 3, 2 / 3])

    def test_scan_ignores_smooth_background_brightness_changes(self) -> None:
        class CountingReader:
            def __init__(self) -> None:
                self.calls = 0

            def read(self, _image_path):
                self.calls += 1
                return [OCRLine("字幕", 0.96, (10, 10, 50, 24))]

        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, background in enumerate((20, 50, 80)):
                image = Image.new("L", (96, 40), background)
                ImageDraw.Draw(image).rectangle((20, 12, 74, 28), fill=245)
                path = Path(directory) / f"frame-{index}.jpg"
                image.save(path, quality=95)
                paths.append(path)
            reader = CountingReader()
            scan_subtitle_frames(
                paths,
                reader=reader,
                config=OCRConfig(sample_fps=3.0, upscale=1.0),
            )

        self.assertEqual(reader.calls, 1)

    def test_scan_detects_changed_subtitle_geometry(self) -> None:
        class CountingReader:
            def __init__(self) -> None:
                self.calls = 0

            def read(self, _image_path):
                self.calls += 1
                return [OCRLine("字幕", 0.96, (10, 10, 50, 24))]

        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, left in enumerate((10, 34)):
                image = Image.new("L", (96, 40), 30)
                ImageDraw.Draw(image).rectangle((left, 12, left + 35, 28), fill=245)
                path = Path(directory) / f"frame-{index}.jpg"
                image.save(path, quality=95)
                paths.append(path)
            reader = CountingReader()
            scan_subtitle_frames(
                paths,
                reader=reader,
                config=OCRConfig(sample_fps=3.0, upscale=1.0),
            )

        self.assertEqual(reader.calls, 2)

    def test_scan_upscales_only_images_sent_to_reader(self) -> None:
        class SizeReader:
            def __init__(self) -> None:
                self.sizes = []

            def read(self, image_path):
                with Image.open(image_path) as image:
                    self.sizes.append(image.size)
                return [OCRLine("字幕", 0.96, (10, 10, 50, 24))]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.jpg"
            Image.new("L", (80, 30), 30).save(path)
            reader = SizeReader()
            scan_subtitle_frames(
                [path],
                reader=reader,
                config=OCRConfig(sample_fps=3.0, upscale=2.0),
            )

        self.assertEqual(reader.sizes, [(160, 60)])

    def test_wide_frames_are_not_upscaled(self) -> None:
        class SizeReader:
            def __init__(self) -> None:
                self.sizes = []

            def read(self, image_path):
                with Image.open(image_path) as image:
                    self.sizes.append(image.size)
                return []

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.jpg"
            Image.new("L", (1080, 300), 30).save(path)
            reader = SizeReader()
            scan_subtitle_frames([path], reader=reader, config=OCRConfig(sample_fps=3.0, upscale=2.0))

        self.assertEqual(reader.sizes, [(1080, 300)])

    def test_inference_scale_reaches_target_width_without_exceeding_upscale(self) -> None:
        config = OCRConfig(upscale=2.0, upscale_below_width=1000)
        self.assertEqual(config.inference_scale(1920), 1.0)
        self.assertAlmostEqual(config.inference_scale(720), 1000 / 720)
        self.assertEqual(config.inference_scale(320), 2.0)

    def test_single_frame_reader_reports_progress_after_every_frame(self) -> None:
        class Reader:
            def read(self, _image_path):
                return []

        progress = []
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, left in enumerate((5, 30, 55)):
                image = Image.new("L", (96, 40), 30)
                ImageDraw.Draw(image).rectangle((left, 12, left + 30, 28), fill=245)
                path = Path(directory) / f"frame-{index}.jpg"
                image.save(path, quality=95)
                paths.append(path)
            scan_subtitle_frames(
                paths,
                reader=Reader(),
                config=OCRConfig(sample_fps=3.0, upscale=1.0),
                progress_callback=lambda done, total: progress.append((done, total)),
            )

        self.assertEqual(progress, [(1, 3), (2, 3), (3, 3)])

    def test_paddle_reader_rejects_unknown_model(self) -> None:
        with self.assertRaises(ValueError):
            PaddleOCRReader(model="large")
        with self.assertRaises(ValueError):
            IsolatedPaddleOCRReader(model="large")

    def test_editor_watermarks_are_not_subtitles(self) -> None:
        for text in ("剪映", "剪映号:KongH", "抖音号 12345"):
            self.assertTrue(is_watermark(text), text)
        for text in ("祝你幸福", "剪映祝你幸福"):
            self.assertFalse(is_watermark(text), text)
        text, _confidence = select_subtitle_text(
            [OCRLine("剪映", 0.99, (10, 10, 60, 30)), OCRLine("祝你幸福", 0.95, (100, 40, 500, 80))], 0.5
        )
        self.assertEqual(text, "祝你幸福")

    def test_default_paddle_reader_runs_outside_the_whisper_process(self) -> None:
        import pipeline.ocr as ocr

        with patch.dict(ocr._DEFAULT_READERS, {}, clear=True):
            self.assertIsInstance(ocr.get_default_reader("paddle", "mobile"), IsolatedPaddleOCRReader)
        with patch.dict(ocr._DEFAULT_READERS, {}, clear=True), patch.dict("os.environ", {"VIDTRANS_OCR_ISOLATED": "0"}), \
                patch.object(ocr, "PaddleOCRReader", lambda model: ("in-process", model)):
            self.assertEqual(ocr.get_default_reader("paddle", "mobile"), ("in-process", "mobile"))

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

    def test_auto_merge_fills_silent_caption_gap_and_keeps_spoken_cues(self) -> None:
        ocr = [{
            "start": 8.8,
            "end": 14.8,
            "text": "有这么完美的女人",
            "ocr_confidence": 0.96,
        }]
        asr = [
            {"start": 6.8, "end": 8.7, "text": "前一句"},
            {"start": 15.2, "end": 16.8, "text": "后一句"},
        ]
        result = merge_ocr_and_asr_segments(ocr, asr)
        self.assertEqual([item["text"] for item in result], ["前一句", "有这么完美的女人", "后一句"])
        self.assertEqual(result[1]["source_method"], "ocr")

    def test_auto_merge_prefers_visible_caption_when_asr_overlaps(self) -> None:
        ocr = [{
            "start": 1.0,
            "end": 2.0,
            "text": "画面字幕",
            "ocr_confidence": 0.95,
        }]
        asr = [{"start": 1.1, "end": 1.9, "text": "错误听写"}]
        result = merge_ocr_and_asr_segments(ocr, asr)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "画面字幕")

    def test_independent_merge_keeps_chinese_caption_over_korean_speech(self) -> None:
        ocr = [{"start": 1.0, "end": 2.0, "text": "这个世界上怎么会"}]
        asr = [
            {"start": 1.1, "end": 1.9, "text": "어떻게 이럴 수가"},
            {"start": 2.5, "end": 3.2, "text": "정말 놀랍다"},
        ]
        result = merge_independent_ocr_asr_segments(
            ocr, asr, ocr_language="zh-CN", asr_language="ko"
        )
        self.assertEqual([item["text"] for item in result], ["这个世界上怎么会", "정말 놀랍다"])
        self.assertEqual([item["source_language"] for item in result], ["zh-CN", "ko"])
        self.assertNotIn("asr_text", result[0])
        self.assertEqual([item["modality"] for item in result], ["visual", "audio"])


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
        # Each sample shows the picture half an interval after its nominal
        # time, so the cue runs until midway to the next (blank) sample.
        self.assertAlmostEqual(cues[0].start, 0.0)
        self.assertAlmostEqual(cues[0].end, 0.8)

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

    def test_merges_fade_in_misread_into_one_caption(self) -> None:
        box = (0.4, 0.5, 0.6, 0.7)
        observations = [
            FrameObservation(0.0, "卜一个", 0.6, box),
            FrameObservation(0.2, "卜一个", 0.7, box),
            FrameObservation(0.4, "下一个", 0.95, box),
            FrameObservation(0.6, "下一个", 0.96, box),
            FrameObservation(0.8, "下一个", 0.97, box),
        ]
        cues = observations_to_cues(observations, self.config)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "下一个")
        self.assertEqual(cues[0].samples, 5)
        self.assertAlmostEqual(cues[0].first_sample, 0.0)
        self.assertAlmostEqual(cues[0].last_sample, 0.8)

    def test_keeps_caption_that_gains_characters_separate(self) -> None:
        box = (0.4, 0.5, 0.6, 0.7)
        observations = [
            FrameObservation(0.0, "爸好看呢", 0.95, box),
            FrameObservation(0.2, "爸好看呢", 0.95, box),
            FrameObservation(0.4, "身体好看呢", 0.95, box),
            FrameObservation(0.6, "身体好看呢", 0.95, box),
        ]
        cues = observations_to_cues(observations, self.config)
        self.assertEqual([cue.text for cue in cues], ["爸好看呢", "身体好看呢"])

    def test_refines_boundaries_to_the_frames_where_caption_changes(self) -> None:
        width, height, rate = 320, 60, 30
        first_visible, last_visible = 17, 44

        def frame(index: int) -> bytes:
            image = Image.new("L", (width, height), 0)
            if first_visible <= index <= last_visible:
                draw = ImageDraw.Draw(image)
                for x in range(100, 220, 10):
                    draw.rectangle((x, 20, x + 4, 40), fill=255)
            return image.tobytes()

        class _FakeProcess:
            def __init__(self, *_args, **_kwargs) -> None:
                import io

                self.stdout = io.BytesIO(b"".join(frame(index) for index in range(60)))

            def kill(self) -> None:
                pass

            def wait(self) -> int:
                return 0

        # Sample k of the 5 fps scan shows source frame 6k + 2, so samples
        # 0.6 s .. 1.4 s are the ones that saw the caption.
        observations = [
            FrameObservation(index / 5, "字幕" if first_visible <= 6 * index + 2 <= last_visible else "", 0.95)
            for index in range(10)
        ]
        config = OCRConfig(sample_fps=5, refine_fps=rate)
        cues = observations_to_cues(observations, config)
        self.assertEqual(len(cues), 1)
        self.assertAlmostEqual(cues[0].start, 0.6)
        self.assertAlmostEqual(cues[0].end, 1.6)

        with patch("pipeline.ocr.subprocess.Popen", _FakeProcess):
            refined = refine_cue_boundaries(
                cues,
                Path("video.mp4"),
                ffmpeg="ffmpeg",
                config=config,
                roi_size=(width, height),
            )

        self.assertAlmostEqual(refined[0].start, first_visible / rate)
        self.assertAlmostEqual(refined[0].end, (last_visible + 1) / rate)


if __name__ == "__main__":
    unittest.main()
