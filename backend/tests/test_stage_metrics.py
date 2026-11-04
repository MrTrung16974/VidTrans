from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pipeline.stage_metrics import measure_stage, merge_stage_metric


class JobCancelled(RuntimeError):
    pass


class StageMetricTests(unittest.TestCase):
    def test_success_records_duration_and_real_time_factor(self) -> None:
        ticks = iter([10.0, 12.5])
        measurements = []
        with measure_stage(
            "asr",
            video_duration=5.0,
            on_complete=measurements.append,
            clock=lambda: next(ticks),
            now=lambda: datetime(2026, 10, 16, tzinfo=timezone.utc),
        ):
            pass

        metric = measurements[0]
        self.assertEqual(metric.status, "completed")
        self.assertEqual(metric.duration_seconds, 2.5)
        self.assertEqual(metric.real_time_factor, 0.5)
        self.assertNotIn("stage", metric.as_dict())

    def test_failure_records_metric_and_propagates_same_error(self) -> None:
        error = RuntimeError("synthetic")
        measurements = []
        with self.assertRaises(RuntimeError) as raised:
            with measure_stage("translation", on_complete=measurements.append):
                raise error
        self.assertIs(raised.exception, error)
        self.assertEqual(measurements[0].status, "failed")

    def test_cancellation_is_classified(self) -> None:
        measurements = []
        with self.assertRaises(JobCancelled):
            with measure_stage("render", on_complete=measurements.append):
                raise JobCancelled("stop")
        self.assertEqual(measurements[0].status, "cancelled")

    def test_callback_failure_does_not_mask_stage_error(self) -> None:
        original = ValueError("pipeline")

        def broken_callback(_measurement) -> None:
            raise RuntimeError("telemetry")

        with self.assertLogs("pipeline.stage_metrics", level="ERROR"):
            with self.assertRaises(ValueError) as raised:
                with measure_stage("ocr", on_complete=broken_callback):
                    raise original
        self.assertIs(raised.exception, original)

    def test_rejects_unknown_stage(self) -> None:
        with self.assertRaises(ValueError):
            with measure_stage("unknown"):
                pass


class MergeStageMetricTests(unittest.TestCase):
    def test_single_run_stage_is_replaced(self) -> None:
        current = {"status": "completed", "duration_seconds": 3.0, "real_time_factor": 1.5}
        self.assertEqual(merge_stage_metric("asr", {"duration_seconds": 99.0}, current), current)

    def test_branch_runs_add_up_with_a_combined_real_time_factor(self) -> None:
        first = merge_stage_metric("asr_branch", None, {"status": "completed", "duration_seconds": 50.0, "real_time_factor": 2.0})
        self.assertEqual((first["runs"], first["media_seconds"], first["real_time_factor"]), (1, 25.0, 2.0))
        both = merge_stage_metric("asr_branch", first, {"status": "completed", "duration_seconds": 300.0, "real_time_factor": 1.5})
        self.assertEqual(both["duration_seconds"], 350.0)
        self.assertEqual(both["runs"], 2)
        self.assertEqual(both["media_seconds"], 225.0)
        self.assertAlmostEqual(float(both["real_time_factor"]), 350.0 / 225.0, places=5)

    def test_branch_run_without_media_length_keeps_no_real_time_factor(self) -> None:
        merged = merge_stage_metric("asr_branch_audio", None, {"status": "failed", "duration_seconds": 4.0})
        self.assertNotIn("real_time_factor", merged)
        self.assertEqual(merged["status"], "failed")


if __name__ == "__main__":
    unittest.main()
