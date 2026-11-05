import unittest

from infrastructure.translation_prompt import glossary_for_lines, parse_glossary, user_prompt
from pipeline.cue_boundaries import repair_cue_boundaries
from pipeline.subtitle_segmentation import drop_duplicate_cues


def cue(start, end, tokens, *, caption=True, **extra):
    step = (end - start) / len(tokens)
    words = [
        {"word": token, "start": round(start + index * step, 3), "end": round(start + (index + 1) * step, 3)}
        for index, token in enumerate(tokens)
    ]
    value = {"start": start, "end": end, "text": "".join(tokens), "words": words, **extra}
    if caption:
        value["timing_source"] = "caption"
    return value


class StaticFixer:
    def __init__(self, lines):
        self.lines = lines
        self.calls = []

    def rebalance_lines(self, lines):
        self.calls.append(list(lines))
        return list(self.lines)


class RepairCueBoundariesTests(unittest.TestCase):
    def test_moves_last_character_back_to_its_sentence(self):
        segments = [
            cue(4.53, 6.40, ["像", "极", "了", "他", "追", "查", "多", "年的", "通", "缉"]),
            cue(6.40, 8.03, ["犯", "虽", "然", "已经", "过", "去了", "十", "多", "年"]),
        ]
        fixer = StaticFixer(["像极了他追查多年的通缉犯", "虽然已经过去了十多年"])

        repaired, changed = repair_cue_boundaries(segments, fixer)

        self.assertEqual(changed, 2)
        self.assertEqual([item["text"] for item in repaired], ["像极了他追查多年的通缉犯", "虽然已经过去了十多年"])
        # Caption-timed cues keep the caption window; the word moves with its timing.
        self.assertEqual((repaired[0]["start"], repaired[0]["end"]), (4.53, 6.40))
        self.assertEqual(repaired[0]["words"][-1]["word"], "犯")

    def test_rejects_changed_characters(self):
        segments = [cue(0.0, 1.0, ["我", "没", "吐"]), cue(1.0, 2.0, ["的", "真", "的"])]
        fixer = StaticFixer(["我没赌的", "真的"])

        repaired, changed = repair_cue_boundaries(segments, fixer)

        self.assertEqual(changed, 0)
        self.assertEqual([item["text"] for item in repaired], ["我没吐", "的真的"])

    def test_one_altered_line_does_not_cost_the_other_repairs(self):
        # Gemini dropped the 林 of line 2 (job 50272532); line 1's repair still applies.
        segments = [
            cue(0.0, 1.0, list("通缉")),
            cue(1.0, 2.0, list("犯这番话点醒了林队林")),
            cue(2.0, 3.0, list("海的东海市")),
            cue(3.0, 4.0, list("四通八达")),
        ]
        fixer = StaticFixer(["通缉犯", "这番话点醒了林队", "海的东海市", "四通八达"])

        repaired, _ = repair_cue_boundaries(segments, fixer)

        self.assertEqual(
            [item["text"] for item in repaired], ["通缉犯", "这番话点醒了林队", "林海的东海市", "四通八达"]
        )

    def test_keeps_break_that_would_split_a_token(self):
        segments = [cue(0.0, 1.0, ["这个", "地方"]), cue(1.0, 2.0, ["很好", "看"])]
        fixer = StaticFixer(["这个地方很", "好看"])

        repaired, changed = repair_cue_boundaries(segments, fixer)

        self.assertEqual(changed, 0)
        self.assertEqual([item["text"] for item in repaired], ["这个地方", "很好看"])

    def test_rejects_large_moves(self):
        segments = [cue(0.0, 1.0, list("一二三四五六")), cue(1.0, 2.0, list("七八九十"))]
        fixer = StaticFixer(["一二", "三四五六七八九十"])

        _, changed = repair_cue_boundaries(segments, fixer)

        self.assertEqual(changed, 0)

    def test_separate_utterances_are_not_joined(self):
        segments = [cue(0.0, 1.0, list("你好")), cue(3.0, 4.0, list("再见"))]
        fixer = StaticFixer(["你", "好再见"])

        repaired, changed = repair_cue_boundaries(segments, fixer)

        self.assertEqual(fixer.calls, [])
        self.assertEqual(changed, 0)
        self.assertEqual([item["text"] for item in repaired], ["你好", "再见"])

    def test_speech_cue_takes_the_timing_of_its_words(self):
        segments = [cue(0.0, 1.0, list("但那张熟悉的"), caption=False), cue(1.0, 1.5, list("脸早就"), caption=False)]
        fixer = StaticFixer(["但那张熟悉的脸", "早就"])

        repaired, changed = repair_cue_boundaries(segments, fixer)

        self.assertEqual(changed, 2)
        self.assertAlmostEqual(repaired[0]["end"], segments[1]["words"][0]["end"])
        self.assertAlmostEqual(repaired[1]["start"], segments[1]["words"][1]["start"])

    def test_failing_fixer_keeps_lines(self):
        class Broken:
            def rebalance_lines(self, lines):
                raise RuntimeError("quota")

        segments = [cue(0.0, 1.0, list("通缉")), cue(1.0, 2.0, list("犯虽然"))]
        repaired, changed = repair_cue_boundaries(segments, Broken())
        self.assertEqual(changed, 0)
        self.assertEqual([item["text"] for item in repaired], ["通缉", "犯虽然"])


class DropDuplicateCuesTests(unittest.TestCase):
    def test_drops_overlapping_copy_and_keeps_tighter_cue(self):
        cues = [
            {"start": 505.8, "end": 507.3, "text": "我才是保安"},
            {"start": 505.8, "end": 510.9, "text": "我才是保安"},
            {"start": 511.0, "end": 512.0, "text": "下一句"},
        ]
        result = drop_duplicate_cues(cues)
        self.assertEqual([(item["start"], item["end"]) for item in result], [(505.8, 507.3), (511.0, 512.0)])

    def test_traditional_and_simplified_copy_is_one_line(self):
        cues = [{"start": 57.2, "end": 58.2, "text": "万一呢"}, {"start": 57.4, "end": 59.2, "text": "萬一呢"}]
        self.assertEqual(len(drop_duplicate_cues(cues)), 1)

    def test_repeated_line_said_twice_stays(self):
        cues = [{"start": 461.3, "end": 462.2, "text": "我只是"}, {"start": 462.1, "end": 463.1, "text": "我只是"}]
        self.assertEqual(len(drop_duplicate_cues(cues)), 2)

    def test_caption_timed_cue_wins(self):
        cues = [
            {"start": 1.0, "end": 2.0, "text": "你好"},
            {"start": 1.1, "end": 2.5, "text": "你好", "timing_source": "caption"},
        ]
        result = drop_duplicate_cues(cues)
        self.assertEqual(result[0].get("timing_source"), "caption")


class GlossaryTests(unittest.TestCase):
    def test_parse_glossary_keeps_valid_terms(self):
        reply = (
            '{"terms": [{"source": "桑延", "heard_as": ["桑言"], "target": "Tang Diên"},'
            ' {"source": "", "target": "x"}, {"source": "美的", "target": "Midea"}]}'
        )
        terms = parse_glossary(reply)
        self.assertEqual(terms, [
            {"source": "桑延", "target": "Tang Diên", "heard_as": ["桑言"]},
            {"source": "美的", "target": "Midea"},
        ])
        self.assertEqual(parse_glossary("not json"), [])

    def test_glossary_for_lines_matches_misheard_spellings(self):
        terms = [{"source": "桑延", "target": "Tang Diên", "heard_as": ["桑言"]}, {"source": "美的", "target": "Midea"}]
        self.assertEqual(glossary_for_lines(terms, ["桑言下了班回来"]), terms[:1])

    def test_user_prompt_includes_glossary(self):
        prompt = user_prompt(["桑言回来了"], None, (), [{"source": "桑延", "target": "Tang Diên"}])
        self.assertIn("Tang Diên", prompt)
        self.assertNotIn("Glossary", user_prompt(["你好"], None))


if __name__ == "__main__":
    unittest.main()
