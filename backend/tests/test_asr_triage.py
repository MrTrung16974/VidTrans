from __future__ import annotations

import unittest

import numpy as np

from pipeline.asr_triage import estimate_snr_db, plan_asr_triage
from pipeline.audio_scene import SceneWindow
from pipeline.multi_branch_asr import BranchTranscript, fuse_branch_transcripts


def seg(start: float, end: float, text: str, probability: float) -> dict:
    return {"start": start, "end": end, "text": text, "avg_logprob": -0.2,
            "words": [{"word": w, "start": start, "end": end, "probability": probability} for w in text.split()]}


def windows(duration: float, **scores_at) -> list[SceneWindow]:
    """10 s windows every 5 s; scores_at maps window start -> (speech, singing, music)."""
    result, start = [], 0.0
    while start < duration:
        end = min(duration, start + 10.0)
        speech, singing, music = scores_at.get(str(int(start)), (0.9, 0.0, 0.0))
        result.append(SceneWindow(start, end, speech, singing, music))
        if end >= duration:
            break
        start += 5.0
    return result


class AsrTriageTests(unittest.TestCase):
    def test_clean_confident_speech_needs_no_second_opinion(self) -> None:
        plan = plan_asr_triage(
            scenes=windows(30),
            segments=[seg(0, 9, "một hai ba", 0.92), seg(9.5, 19, "bốn năm sáu", 0.9), seg(19.5, 29.5, "bảy tám", 0.95)],
            duration=30,
        )
        self.assertFalse(plan.needed)

    def test_speech_over_music_is_checked_and_separated(self) -> None:
        scenes = windows(60, **{"20": (0.8, 0.0, 0.9), "25": (0.8, 0.0, 0.9)})
        segments = [seg(t, t + 4, "câu nói rõ ràng", 0.9) for t in range(0, 60, 5)]
        plan = plan_asr_triage(scenes=scenes, segments=segments, duration=60)
        self.assertEqual(len(plan.regions), 1)
        region = plan.regions[0]
        self.assertIn("speech_over_music", region.reasons)
        self.assertLessEqual(region.start, 22.5)
        self.assertGreaterEqual(region.end, 32.5)
        self.assertEqual(plan.branches_for(region), (True, False))
        # Regions snap to whole Whisper lines: no recognised line is cut in half.
        for s in segments:
            inside = region.start <= s["start"] and s["end"] <= region.end
            outside = s["end"] <= region.start or s["start"] >= region.end
            self.assertTrue(inside or outside)

    def test_low_confidence_missed_speech_and_hallucination_are_found(self) -> None:
        scenes = windows(60, **{"40": (0.05, 0.0, 0.2), "45": (0.05, 0.0, 0.2)})
        segments = [seg(0, 4, "ổn định", 0.9), seg(5, 8, "lờ mờ", 0.3), seg(45, 48, "Hãy đăng ký kênh", 0.4)]
        plan = plan_asr_triage(scenes=scenes, segments=segments, duration=60, max_seconds=600)
        reasons = set().union(*(r.reasons for r in plan.regions))
        self.assertTrue({"low_confidence", "missed_speech", "possible_hallucination"} <= reasons)
        self.assertEqual(plan.hallucination_spans, [(45.0, 48.0)])

    def test_mostly_doubtful_short_video_is_checked_whole(self) -> None:
        scenes = windows(20, **{"0": (0.8, 0.5, 0.9), "5": (0.8, 0.5, 0.9), "10": (0.8, 0.5, 0.9)})
        plan = plan_asr_triage(scenes=scenes, segments=[seg(1, 19, "nói trên nhạc", 0.9)], duration=20)
        self.assertTrue(plan.whole_file)

    def test_budget_keeps_the_most_severe_regions(self) -> None:
        segments = [seg(t, t + 3, "mơ hồ", 0.5) for t in range(0, 600, 30)]
        segments[5] = seg(150, 153, "rất mơ hồ", 0.1)
        plan = plan_asr_triage(scenes=[], segments=segments, duration=600, max_seconds=6)
        self.assertEqual(len(plan.regions), 1)
        self.assertLessEqual(plan.regions[0].start, 150)
        self.assertGreater(plan.skipped_regions, 0)

    def test_snr_separates_clean_and_noisy_speech(self) -> None:
        rate = 16000
        rng = np.random.default_rng(0)
        t = np.arange(rate * 4) / rate
        voice = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        voice[: rate * 2] = 0
        clean = voice + rng.normal(0, 0.001, len(t)).astype(np.float32)
        noisy = voice + rng.normal(0, 0.2, len(t)).astype(np.float32)
        self.assertGreater(estimate_snr_db(clean, rate, [(2.0, 4.0)]), 30)
        self.assertLess(estimate_snr_db(noisy, rate, [(2.0, 4.0)]), 15)


class FusionGuardTests(unittest.TestCase):
    def test_truncated_alternative_never_replaces_a_whole_line(self) -> None:
        result = fuse_branch_transcripts([
            BranchTranscript("original", [seg(0, 6, "hôm nay chúng ta bắt đầu bài học", 0.5)]),
            BranchTranscript("separated", [seg(4.5, 6, "bài học", 0.95)]),
        ])
        self.assertEqual(result.segments[0]["asr_branch"], "original")

    def test_hallucination_dropped_only_when_all_signals_agree(self) -> None:
        branches = [
            BranchTranscript("original", [seg(0, 3, "xin chào", 0.9), seg(45, 48, "Hãy đăng ký kênh", 0.3)]),
            BranchTranscript("separated", [seg(0, 3, "xin chào", 0.9)]),
        ]
        dropped = fuse_branch_transcripts(branches, checked_spans=[(0, 60)], hallucination_spans=[(45, 48)])
        self.assertEqual([s["text"] for s in dropped.segments], ["xin chào"])
        kept = fuse_branch_transcripts(branches, checked_spans=[(0, 60)])
        self.assertEqual(len(kept.segments), 2)


if __name__ == "__main__":
    unittest.main()
