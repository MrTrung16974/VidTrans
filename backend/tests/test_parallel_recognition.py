from __future__ import annotations

import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
from infrastructure.media_probe import MediaProbe

OCR_SEGMENTS = [{"start": 0.0, "end": 1.0, "text": "你好", "source_method": "ocr"}]
ASR_SEGMENTS = [{"start": 0.0, "end": 1.0, "text": "你好", "source_method": "speech"}]


class ParallelSourceRecognitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.updates: list[dict] = []
        self.merged: list[tuple] = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(main, "update_job", lambda job_id, **fields: self.updates.append(fields)))
        stack.enter_context(patch.object(main, "ensure_job_active", lambda job_id: None))
        stack.enter_context(patch.object(main, "record_job_stage_measurement", lambda job_id, measurement: None))
        stack.enter_context(patch.object(main, "SETTINGS", SimpleNamespace(ocr_backend="tesseract", ocr_model="m")))
        stack.enter_context(patch.object(main, "AUDIO_SCENE_ENABLED", False))
        stack.enter_context(patch.object(main, "segment_subtitle_cues", lambda segments, options=None: segments))
        stack.enter_context(patch.object(main, "filter_meaningful_segments", lambda segments: segments))
        stack.enter_context(patch.object(
            main, "merge_ocr_and_asr_segments",
            lambda ocr, asr: self.merged.append((ocr, asr)) or [*ocr, *asr],
        ))

    def recognize(self, subtitle_source: str = "auto", *, has_audio: bool = True):
        return main.recognize_video_source(
            job_id="job-parallel",
            video_path=Path("v.mp4"),
            work_dir=Path("work"),
            source_probe=MediaProbe(duration=10.0, width=0, height=0, fps=30.0, has_audio=has_audio),
            subtitle_source=subtitle_source,
            source_language="zh-CN",
            whisper_model="small",
            ocr_sample_fps=3.0,
            ocr_roi_top=0.6,
            ocr_roi_bottom=1.0,
        )

    def test_ocr_and_asr_overlap_and_merge_the_same_inputs(self) -> None:
        both_running = threading.Barrier(2, timeout=5)

        def ocr(*args, progress_callback, **kwargs):
            both_running.wait()  # Deadlocks (BrokenBarrierError) if the stages run one after another.
            progress_callback(10, 10)
            return list(OCR_SEGMENTS)

        def asr(model, path, language, job_id, duration, *, progress, **kwargs):
            both_running.wait()
            progress(10.0, 10.0)
            return list(ASR_SEGMENTS), "zh-CN", 0.99

        with patch.object(main, "extract_burned_subtitle_segments", ocr), patch.object(main, "transcribe_video", asr):
            segments, language, probability, method, skipped = self.recognize()

        self.assertEqual(self.merged, [(OCR_SEGMENTS, ASR_SEGMENTS)])
        self.assertEqual(segments, [*OCR_SEGMENTS, *ASR_SEGMENTS])
        self.assertEqual((language, probability, method, skipped), ("zh-CN", 0.99, "hybrid", 0))
        self.assertAlmostEqual(self.updates[-1]["progress"], 0.38)

    def test_required_ocr_failure_stops_asr_and_raises_the_ocr_error(self) -> None:
        asr_started = threading.Event()
        asr_reports: list[float] = []

        def ocr(*args, **kwargs):
            asr_started.wait(5)
            raise RuntimeError("tesseract missing")

        def asr(model, path, language, job_id, duration, *, progress, **kwargs):
            asr_started.set()
            for step in range(1, 10_000):
                asr_reports.append(step)
                progress(step / 1000, 10.0)
                threading.Event().wait(0.001)
            return list(ASR_SEGMENTS), "zh-CN", 0.99

        with patch.object(main, "extract_burned_subtitle_segments", ocr), patch.object(main, "transcribe_video", asr):
            with self.assertRaisesRegex(RuntimeError, "tesseract missing"):
                self.recognize("burned")
        self.assertLess(len(asr_reports), 9_999)

    def test_optional_ocr_failure_falls_back_to_speech(self) -> None:
        def ocr(*args, **kwargs):
            raise RuntimeError("ocr crashed")

        def asr(model, path, language, job_id, duration, *, progress, **kwargs):
            return list(ASR_SEGMENTS), "zh-CN", 0.99

        with patch.object(main, "extract_burned_subtitle_segments", ocr), patch.object(main, "transcribe_video", asr):
            segments, _language, _probability, method, _skipped = self.recognize("auto")

        self.assertEqual((segments, method), (ASR_SEGMENTS, "speech"))

    def test_cancellation_in_asr_propagates(self) -> None:
        def ocr(*args, progress_callback, **kwargs):
            for step in range(1, 10_000):
                progress_callback(step, 10_000)
                threading.Event().wait(0.001)
            return list(OCR_SEGMENTS)

        def asr(*args, **kwargs):
            raise main.JobCancelled("stop")

        with patch.object(main, "extract_burned_subtitle_segments", ocr), patch.object(main, "transcribe_video", asr):
            with self.assertRaises(main.JobCancelled):
                self.recognize()

    def test_video_without_audio_keeps_the_sequential_ocr_path(self) -> None:
        calls: list[str] = []

        def ocr(*args, progress_callback, **kwargs):
            calls.append(threading.current_thread().name)
            return list(OCR_SEGMENTS)

        with patch.object(main, "extract_burned_subtitle_segments", ocr), patch.object(main, "transcribe_video") as asr:
            segments, _language, _probability, method, _skipped = self.recognize("burned", has_audio=False)

        asr.assert_not_called()
        self.assertEqual(calls, [threading.current_thread().name])
        self.assertEqual(method, "ocr")

    def test_speech_only_job_reads_captions_when_speech_covers_little_of_the_video(self) -> None:
        def ocr(*args, progress_callback, **kwargs):
            progress_callback(1, 1)
            return list(OCR_SEGMENTS)

        def asr(model, path, language, job_id, duration, *, progress, **kwargs):
            return list(ASR_SEGMENTS), "zh-CN", 0.99

        with patch.object(main, "extract_burned_subtitle_segments", ocr), patch.object(main, "transcribe_video", asr):
            segments, _language, _probability, method, _skipped = self.recognize("speech")

        self.assertEqual(self.merged, [(OCR_SEGMENTS, ASR_SEGMENTS)])
        self.assertEqual(method, "hybrid")

    def test_speech_only_job_skips_ocr_when_speech_covers_the_video(self) -> None:
        speech = [{"start": float(i), "end": float(i + 1), "text": "你好"} for i in range(8)]

        def asr(model, path, language, job_id, duration, *, progress, **kwargs):
            return list(speech), "zh-CN", 0.99

        with patch.object(main, "extract_burned_subtitle_segments") as ocr, patch.object(main, "transcribe_video", asr):
            segments, _language, _probability, method, _skipped = self.recognize("speech")

        ocr.assert_not_called()
        self.assertEqual((segments, method), (speech, "speech"))


class SourceRecognitionProgressTests(unittest.TestCase):
    def test_combined_progress_never_moves_backwards_when_asr_retries(self) -> None:
        updates: list[dict] = []
        with patch.object(main, "update_job", lambda job_id, **fields: updates.append(fields)), patch.object(
            main, "ensure_job_active", lambda job_id: None
        ):
            tracker = main.SourceRecognitionProgress("job", 10.0)
            tracker.report_asr(8.0, 10.0)
            tracker.report_ocr(50, 100)
            tracker.report_asr(1.0, 10.0)  # Retry pass restarts from the beginning.

        progress = [update["progress"] for update in updates]
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(updates[1]["step_detail"], "OCR 50/100 frames · Đã nhận diện 0:08 / 0:10")

    def test_aborted_tracker_stops_the_sibling_stage(self) -> None:
        with patch.object(main, "update_job"), patch.object(main, "ensure_job_active", lambda job_id: None):
            tracker = main.SourceRecognitionProgress("job", 10.0)
            tracker.aborted.set()
            with self.assertRaises(main.SiblingStageFailed):
                tracker.report_ocr(1, 10)


if __name__ == "__main__":
    unittest.main()
