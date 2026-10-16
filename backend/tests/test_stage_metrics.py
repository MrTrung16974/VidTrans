from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pipeline.stage_metrics import measure_stage


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


if __name__ == "__main__":
    unittest.main()
