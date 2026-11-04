from __future__ import annotations

import unittest

from pipeline.multi_branch_asr import (
    BranchTranscript,
    fuse_branch_transcripts,
    segment_confidence,
    text_similarity,
)


def seg(start: float, end: float, text: str, probability: float) -> dict:
    return {"start": start, "end": end, "text": text, "avg_logprob": -0.3,
            "words": [{"word": w, "start": start, "end": end, "probability": probability} for w in text.split()]}


class MultiBranchFusionTests(unittest.TestCase):
    def test_similarity_ignores_case_punctuation_and_spacing(self) -> None:
        self.assertEqual(text_similarity("Hôm nay, chúng ta bắt đầu!", "hôm nay chúng ta bắt đầu"), 1.0)
        self.assertEqual(text_similarity("今天我们开始。", "今天 我们 开始"), 1.0)

    def test_confidence_falls_back_to_avg_logprob(self) -> None:
        self.assertAlmostEqual(segment_confidence({"avg_logprob": 0.0}), 1.0)

    def test_agreeing_branches_keep_the_original(self) -> None:
        result = fuse_branch_transcripts([
            BranchTranscript("original", [seg(0, 2, "Hôm nay chúng ta bắt đầu", 0.7)]),
            BranchTranscript("separated", [seg(0.1, 2, "hôm nay chúng ta bắt đầu", 0.9)]),
        ])
        self.assertEqual([s["asr_branch"] for s in result.segments], ["original"])
        self.assertEqual(result.review, [])

    def test_clearly_more_confident_stem_replaces_and_is_reported(self) -> None:
        result = fuse_branch_transcripts([
            BranchTranscript("original", [seg(0, 2, "em ơi đừng khóc", 0.4)]),
            BranchTranscript("separated", [seg(0, 2, "hôm nay chúng ta bắt đầu", 0.85)]),
        ])
        self.assertEqual(result.segments[0]["text"], "hôm nay chúng ta bắt đầu")
        self.assertEqual(result.review[0]["kind"], "disagreement")
        self.assertEqual(result.review[0]["chosen"], "separated")
        self.assertEqual(result.summary()["replaced"], 1)

    def test_small_confidence_edge_keeps_original_but_reports(self) -> None:
        result = fuse_branch_transcripts([
            BranchTranscript("original", [seg(0, 2, "xin chào mọi người", 0.8)]),
            BranchTranscript("separated", [seg(0, 2, "xin cháo mọi ngừơi ơi", 0.82)]),
            BranchTranscript("enhanced", [seg(0, 2, "tin chào mọi", 0.6)]),
        ])
        self.assertEqual(result.segments[0]["asr_branch"], "original")
        self.assertEqual(result.review[0]["chosen"], "original")

    def test_speech_only_a_stem_hears_is_added_outside_music(self) -> None:
        result = fuse_branch_transcripts(
            [
                BranchTranscript("original", [seg(0, 2, "một", 0.9)]),
                BranchTranscript("separated", [seg(0, 2, "một", 0.9), seg(5, 7, "hai ba", 0.8), seg(10, 12, "lời hát", 0.95)]),
            ],
            music_spans=[(9.0, 13.0)],
        )
        self.assertEqual([s["text"] for s in result.segments], ["một", "hai ba"])
        kinds = {item["kind"] for item in result.review}
        self.assertEqual(kinds, {"added_from_stem", "stem_only_in_music"})

    def test_single_branch_is_returned_unchanged(self) -> None:
        segments = [seg(0, 1, "a", 0.5)]
        result = fuse_branch_transcripts([BranchTranscript("original", segments)])
        self.assertEqual(result.segments, segments)


if __name__ == "__main__":
    unittest.main()
