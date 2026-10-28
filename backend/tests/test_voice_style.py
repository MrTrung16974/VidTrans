import unittest

from pipeline.tts_timing import MAX_SYNTHESIS_RATE_SLOWDOWN_PERCENT, relaxed_synthesis_rate_percent
from pipeline.voice_style import infer_speaker_voice, infer_voice_style


def cue(source: str, text: str, start: float = 0.0, end: float = 2.0) -> dict:
    return {"source_text": source, "text": text, "start": start, "end": end}


class SpeakerVoiceTests(unittest.TestCase):
    def test_em_to_anh_dialogue_means_female_narrator(self) -> None:
        segments = [
            cue("本來是挺愛你的", "Vốn dĩ là rất yêu anh"),
            cue("我不該看到的東西", "Em không nên thấy"),
            cue("我甚至在想", "Em thậm chí còn đang nghĩ"),
            cue("你沒瞞住", "Anh giấu không kỹ"),
        ]
        self.assertEqual(infer_speaker_voice(segments, "vi")[0], "female")

    def test_anh_to_em_dialogue_means_male_narrator(self) -> None:
        segments = [
            cue("我好想你", "Anh nhớ em lắm"),
            cue("我會等", "Anh sẽ đợi"),
            cue("你別哭", "Em đừng khóc"),
        ]
        self.assertEqual(infer_speaker_voice(segments, "vi")[0], "male")

    def test_neutral_pronouns_give_no_opinion(self) -> None:
        segments = [cue("我覺得", "Tôi nghĩ"), cue("你看", "Bạn xem")]
        self.assertEqual(infer_speaker_voice(segments, "vi"), (None, 0))

    def test_chinese_spouse_terms_name_the_speaker(self) -> None:
        segments = [cue("老婆你看", "Vợ ơi xem này"), cue("老婆吃飯", "Vợ ơi ăn cơm")]
        self.assertEqual(infer_speaker_voice(segments, "en")[0], "male")


class VoiceStyleTests(unittest.TestCase):
    def test_sad_confession_picks_gentle(self) -> None:
        segments = [
            cue("本來是挺愛你的", "Vốn dĩ là rất yêu anh", 0, 2.2),
            cue("一切都挺沒意思的", "Mọi thứ đều thật vô vị", 2.2, 5.3),
            cue("可惜你沒瞞住", "Đáng tiếc anh giấu không kỹ", 5.3, 7.9),
            cue("我的心", "Trái tim em", 7.9, 8.6),
        ]
        self.assertEqual(infer_voice_style(segments).style, "gentle")

    def test_sales_pitch_picks_commercial(self) -> None:
        segments = [
            cue("今天優惠價格", "Hôm nay giá ưu đãi", 0, 1),
            cue("點擊鏈接下單", "Bấm link đặt hàng", 1, 2),
            cue("包郵", "Miễn phí vận chuyển", 2, 3),
        ]
        self.assertEqual(infer_voice_style(segments).style, "commercial")

    def test_one_soft_cue_in_a_slow_clip_picks_gentle(self) -> None:
        segments = [
            cue("回到你身邊", "Quay trở lại bên cạnh em", 0, 2.0),
            cue("我只在乎", "Tôi chỉ quan tâm", 2.0, 4.0),
            cue("想你", "Nhớ em", 4.0, 5.5),
            cue("我自己", "Chính tôi", 5.5, 7.0),
        ]
        self.assertEqual(infer_voice_style(segments).style, "gentle")

    def test_slow_source_without_keywords_reads_gently(self) -> None:
        segments = [cue("我們去公園", "Chúng ta đi công viên", 0, 2.0), cue("天氣不錯", "Thời tiết đẹp", 2.0, 3.6)]
        self.assertEqual(infer_voice_style(segments).style, "gentle")

    def test_plain_content_stays_natural(self) -> None:
        segments = [cue("我們去公園", "Chúng ta đi công viên", 0, 1.2), cue("天氣不錯", "Thời tiết đẹp", 1.2, 2.2)]
        self.assertEqual(infer_voice_style(segments).style, "natural")

    def test_vietnamese_keywords_match_whole_words_only(self) -> None:
        # "mùa" / "giá" style substrings must not trigger unrelated styles.
        segments = [cue("", "Gia đình đi du lịch mùa hè", 0, 3), cue("", "Cả nhà ăn tối", 3, 5)]
        self.assertEqual(infer_voice_style(segments).style, "natural")


class RelaxedRateTests(unittest.TestCase):
    def test_short_line_in_long_window_is_slowed_within_limit(self) -> None:
        self.assertEqual(relaxed_synthesis_rate_percent(0.9, 3.0), -MAX_SYNTHESIS_RATE_SLOWDOWN_PERCENT)
        self.assertLess(relaxed_synthesis_rate_percent(1.5, 2.5), 0)

    def test_line_that_already_fills_window_is_untouched(self) -> None:
        self.assertEqual(relaxed_synthesis_rate_percent(2.0, 2.5), 0)
        self.assertEqual(relaxed_synthesis_rate_percent(0.0, 2.5), 0)
