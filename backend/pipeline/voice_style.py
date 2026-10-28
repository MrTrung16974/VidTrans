"""Content cues for automatic voice choice.

Pitch alone cannot tell a low female voice from a high male one, and says
nothing about mood. These helpers read the source and translated text (plus
the source speaking pace) to pick a voice style and, where the dialogue makes
it explicit, the narrator's voice type. Everything is local and lexicon based
so it works without any AI provider.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable

AUTO_VOICE_STYLE = "auto"
DEFAULT_VOICE_STYLE = "natural"

# Share of cues that must carry a style's keywords before it is chosen.
MIN_STYLE_SHARE = 0.15
MIN_STYLE_HITS = 2
SOFT_STYLES = frozenset({"gentle", "storyteller", "calm"})
# Source pace, in characters/second for CJK and words/second otherwise.
SLOW_CJK_RATE = 4.5
FAST_CJK_RATE = 6.5
SLOW_WORD_RATE = 2.0
FAST_WORD_RATE = 3.6
PACE_BONUS = 0.10
# A clearly unhurried source is read gently even without keywords.
CLEARLY_SLOW_PACE_SHARE = 0.8
EXCLAMATION_SHARE = 0.3

STYLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    # Confession, romance, sadness, healing.
    "gentle": (
        "yêu", "thương", "nhớ", "buồn", "khóc", "đau lòng", "trái tim", "cô đơn", "tổn thương",
        "chia tay", "người yêu", "kỷ niệm", "đáng tiếc", "tiếc nuối", "chữa lành",
        "愛", "爱", "想你", "難過", "难过", "哭", "我的心", "孤單", "孤单", "分手", "傷心", "伤心",
        "喜歡", "喜欢", "遺憾", "遗憾", "可惜", "心疼", "回憶", "回忆",
        "love", "miss you", "heartbreak", "lonely", "tears",
    ),
    "storyteller": (
        "ngày xưa", "câu chuyện", "truyền thuyết", "năm đó", "thuở", "kể từ đó",
        "從前", "从前", "故事", "傳說", "传说", "當年", "当年", "那一年",
        "once upon", "long ago", "legend",
    ),
    "news": (
        "phóng viên", "chính phủ", "cảnh sát", "theo thông tin", "ghi nhận", "báo cáo", "sự việc",
        "報道", "报道", "記者", "记者", "政府", "警方", "據悉", "据悉", "消息稱", "消息称", "通報", "通报",
        "reported", "government", "police", "according to",
    ),
    "commercial": (
        "giảm giá", "khuyến mãi", "đặt hàng", "sản phẩm", "ưu đãi", "miễn phí", "chỉ còn", "mua ngay",
        "價格", "价格", "優惠", "优惠", "下單", "下单", "購買", "购买", "產品", "产品", "包郵", "包邮",
        "鏈接", "链接", "限時", "限时",
        "discount", "buy now", "free shipping", "limited offer",
    ),
    "cheerful": (
        "haha", "vui quá", "tuyệt vời", "quá đỉnh", "đỉnh thật", "hạnh phúc quá",
        "哈哈", "太好了", "開心", "开心", "好棒", "厲害", "厉害", "太好吃",
        "awesome", "so happy", "amazing",
    ),
    "calm": (
        "nguyên nhân", "kiến thức", "khoa học", "phương pháp", "các bước", "lưu ý",
        "為什麼", "为什么", "原因", "方法", "步驟", "步骤", "知識", "知识", "科學", "科学",
        "the reason", "step by step", "science",
    ),
}

_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")

# Vietnamese pronouns that reveal who is speaking to whom.
VI_SELF_PRONOUNS = ("em", "anh", "chị")
ZH_FIRST_PERSON = ("我",)
ZH_SECOND_PERSON = ("你", "妳")
# Addressing a husband / wife, or the coy self-reference 人家, names the speaker.
ZH_SPEAKER_CUES = {"老公": "female", "人家": "female", "老婆": "male", "媳婦": "male", "媳妇": "male"}
MIN_SPEAKER_CUES = 2


@dataclass
class StyleDecision:
    style: str
    scores: dict[str, float] = field(default_factory=dict)
    pace: float | None = None
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "style": self.style,
            "scores": {key: round(value, 3) for key, value in self.scores.items() if value},
            "source_pace": round(self.pace, 2) if self.pace is not None else None,
            "reasons": self.reasons,
        }


def _contains(text: str, keyword: str) -> bool:
    if _CJK.search(keyword):
        return keyword in text
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) is not None


def _cue_texts(segment: dict[str, Any]) -> str:
    return f"{segment.get('source_text') or ''}\n{segment.get('text') or ''}".lower()


def source_pace(segments: Iterable[dict[str, Any]]) -> tuple[float | None, bool]:
    """Median speaking rate of the source and whether it is counted in CJK characters."""

    rates: list[float] = []
    cjk_votes = 0
    for segment in segments:
        text = str(segment.get("source_text") or "").strip()
        duration = float(segment.get("end") or 0) - float(segment.get("start") or 0)
        if not text or duration < 0.4:
            continue
        cjk_chars = len(_CJK.findall(text))
        if cjk_chars:
            cjk_votes += 1
            rates.append(cjk_chars / duration)
        else:
            rates.append(len(text.split()) / duration)
    if not rates:
        return None, False
    return statistics.median(rates), cjk_votes * 2 >= len(rates)


def infer_voice_style(segments: list[dict[str, Any]]) -> StyleDecision:
    """Pick a voice preset that suits the meaning and delivery of the video."""

    spoken = [segment for segment in segments if not segment.get("skip_tts")]
    if not spoken:
        return StyleDecision(DEFAULT_VOICE_STYLE, reasons=["no_speech"])

    hits = {style: 0 for style in STYLE_KEYWORDS}
    for segment in spoken:
        text = _cue_texts(segment)
        for style, keywords in STYLE_KEYWORDS.items():
            if any(_contains(text, keyword) for keyword in keywords):
                hits[style] += 1
    scores = {style: count / len(spoken) for style, count in hits.items()}
    reasons = [f"{style}:{count}/{len(spoken)}" for style, count in hits.items() if count]

    pace, cjk = source_pace(spoken)
    if pace is not None:
        slow, fast = (SLOW_CJK_RATE, FAST_CJK_RATE) if cjk else (SLOW_WORD_RATE, FAST_WORD_RATE)
        if pace <= slow:
            scores["gentle"] += PACE_BONUS
            scores["storyteller"] += PACE_BONUS / 2
            scores["calm"] += PACE_BONUS / 2
            reasons.append("slow_source_pace")
        elif pace >= fast:
            scores["commercial"] += PACE_BONUS / 2
            scores["cheerful"] += PACE_BONUS / 2
            scores["energetic"] = PACE_BONUS
            reasons.append("fast_source_pace")

    exclaimed = sum(1 for segment in spoken if re.search(r"[!！]", _cue_texts(segment)))
    if exclaimed / len(spoken) >= EXCLAMATION_SHARE:
        scores["energetic"] = scores.get("energetic", 0.0) + PACE_BONUS
        scores["cheerful"] += PACE_BONUS / 2
        reasons.append("exclamations")

    best = max(scores, key=lambda style: scores[style])
    # A soft, expressive read suits more videos than a pushy one, so soft
    # styles need less evidence than the brisk ones.
    min_hits = 1 if best in SOFT_STYLES else MIN_STYLE_HITS
    keyword_backed = hits.get(best, 0) >= min_hits
    # Pace or punctuation alone is weak evidence; it may choose energetic
    # (which has no keywords) but only once clearly above the bonus itself.
    if scores[best] >= MIN_STYLE_SHARE and (keyword_backed or best == "energetic" and scores[best] > PACE_BONUS):
        return StyleDecision(best, scores, pace, reasons)
    if pace is not None and pace <= CLEARLY_SLOW_PACE_SHARE * (SLOW_CJK_RATE if cjk else SLOW_WORD_RATE):
        return StyleDecision("gentle", scores, pace, reasons)
    return StyleDecision(DEFAULT_VOICE_STYLE, scores, pace, reasons)


def _first_vi_pronoun(text: str) -> str | None:
    match = re.search(rf"(?<!\w)({'|'.join(VI_SELF_PRONOUNS)})(?!\w)", text.lower())
    return match.group(1) if match else None


def infer_speaker_voice(segments: list[dict[str, Any]], target_language: str) -> tuple[str | None, int]:
    """Return the narrator's voice type implied by how they address people.

    In Vietnamese a woman talking to a man calls herself "em" and him "anh";
    a man does the reverse. Lines that only contain "I" (or only "you") in the
    Chinese source tell us which pronoun is the speaker. Returns the voice and
    the number of supporting cues, or (None, 0) when the text is not explicit.
    """

    votes = {"male": 0, "female": 0}
    self_terms: dict[str, int] = {}
    other_terms: dict[str, int] = {}
    for segment in segments:
        source = str(segment.get("source_text") or "")
        for cue, voice in ZH_SPEAKER_CUES.items():
            if cue in source:
                votes[voice] += 1
        if target_language != "vi":
            continue
        has_self = any(token in source for token in ZH_FIRST_PERSON)
        has_other = any(token in source for token in ZH_SECOND_PERSON)
        pronoun = _first_vi_pronoun(str(segment.get("text") or ""))
        if pronoun is None or has_self == has_other:
            continue
        bucket = self_terms if has_self else other_terms
        bucket[pronoun] = bucket.get(pronoun, 0) + 1

    # "em" + "anh" is the couple register; "chị" as self is always female.
    votes["female"] += min(self_terms.get("em", 0), 1) * other_terms.get("anh", 0) + self_terms.get("em", 0) * min(
        other_terms.get("anh", 0), 1
    )
    votes["male"] += min(self_terms.get("anh", 0), 1) * other_terms.get("em", 0) + self_terms.get("anh", 0) * min(
        other_terms.get("em", 0), 1
    )
    votes["female"] += self_terms.get("chị", 0)

    leader = max(votes, key=lambda voice: votes[voice])
    other = "male" if leader == "female" else "female"
    if votes[leader] >= MIN_SPEAKER_CUES and votes[leader] >= 2 * votes[other]:
        return leader, votes[leader]
    return None, 0
