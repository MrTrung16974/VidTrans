"""Background-music categories, guessed from a track's file name.

Admins upload many tracks at once; each is filed under a category from words in its
name (Vietnamese with or without accents, English, Chinese), so users can pick music by
mood. An explicit ``[Thể loại]`` prefix in the name wins, and admins can change it later.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

OTHER = "Khác"

# (category, keywords). Keywords are matched on the accent-free, lower-cased name as whole
# words (Latin) or substrings (Chinese). Earlier categories win when several match, so the
# more specific moods come before the broad ones.
CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Kinh dị / Hồi hộp", (
        "kinh di", "rung ron", "hoi hop", "ma quai", "con ma", "bong ma", "ma am", "quy di", "horror", "scary", "creepy", "suspense", "thriller", "spooky",
        "dark ambient", "恐怖", "惊悚", "悬疑", "紧张",
    )),
    ("Hành động / Kịch tính", (
        "hanh dong", "kich tinh", "chien dau", "danh nhau", "cao trao", "hung trang", "action", "epic", "battle",
        "fight", "war", "trailer", "intense", "heroic", "dramatic", "燃", "战斗", "史诗", "激昂", "热血",
    )),
    ("Buồn / Cảm động", (
        "buon", "cam dong", "tam trang", "chia tay", "that tinh", "co don", "sad", "emotional", "melancholy",
        "tears", "lonely", "heartbreak", "伤感", "悲伤", "催泪", "难过", "孤独",
    )),
    ("Lãng mạn / Tình cảm", (
        "lang man", "tinh cam", "tinh yeu", "hen ho", "romantic", "romance", "love", "wedding", "sweet",
        "浪漫", "甜蜜", "爱情", "告白",
    )),
    ("Hài hước / Vui nhộn", (
        "hai huoc", "nhac hai", "nhac che", "vui nhon", "vui ve", "nham nhi", "funny", "comedy", "comic", "quirky", "silly",
        "cartoon", "meme", "搞笑", "滑稽", "欢快", "沙雕",
    )),
    ("Cổ trang / Trung Hoa", (
        "co trang", "co phong", "kiem hiep", "tien hiep", "trung hoa", "trung quoc", "guzheng", "erhu", "pipa",
        "chinese", "oriental", "ancient", "kungfu", "kung fu", "wuxia", "xianxia", "古风", "古筝", "二胡", "琵琶",
        "武侠", "仙侠", "国风",
    )),
    ("Lofi / Thư giãn", (
        "lofi", "lo fi", "chill", "chillout", "thu gian", "nhe nhang", "em diu", "relax", "relaxing", "calm",
        "ambient", "sleep", "study", "cafe", "coffee", "jazz", "acoustic", "piano", "guitar", "peaceful",
        "轻音乐", "放松", "治愈", "安静", "钢琴",
    )),
    ("Sôi động / EDM", (
        "soi dong", "remix", "edm", "dance", "party", "phonk", "hiphop", "hip hop", "rap", "trap", "electro",
        "house", "techno", "dubstep", "upbeat", "energetic", "workout", "动感", "电音", "嗨",
        "蹦迪", "说唱",
    )),
    ("Truyền cảm hứng / Vlog", (
        "truyen cam hung", "dong luc", "tich cuc", "vlog", "travel", "du lich", "inspiring", "inspirational",
        "motivation", "motivational", "corporate", "uplifting", "happy", "bright", "summer", "励志", "旅行",
        "阳光", "快乐",
    )),
)
CATEGORIES: tuple[str, ...] = tuple(name for name, _ in CATEGORY_KEYWORDS) + (OTHER,)
MAX_CATEGORY_CHARS = 40

_EXPLICIT_PREFIX = re.compile(r"^\s*[\[【]\s*([^\]】]{1,40}?)\s*[\]】]\s*")
# Uploader noise that is not part of a track's title.
_NOISE = re.compile(
    r"[\[(【（][^\])】）]*(official|audio|lyrics?|lyric video|mv|m/v|hd|hq|4k|\d{2,3}\s*kbps|free|no copyright|"
    r"ncs|copyright free|royalty free|bản quyền|ban quyen|nhạc nền|nhac nen)[^\])】）]*[\])】）]",
    re.IGNORECASE,
)
_BITRATE = re.compile(r"\b\d{2,3}\s*kbps\b", re.IGNORECASE)


def fold(text: str) -> str:
    """Lower-case, accents removed (đ -> d), every run of separators turned into one space."""
    decomposed = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(re.sub(r"[_\-.,;:!?+|/\\()\[\]{}'\"#&~*=]+", " ", plain.lower()).split())


def _canonical(name: str) -> str | None:
    """A known category from a free-form name ("buồn", "EDM", "Kinh dị / Hồi hộp"), or None."""
    folded = fold(name)
    if not folded:
        return None
    for category in CATEGORIES:
        if folded == fold(category) or folded in {fold(part) for part in category.split("/")}:
            return category
    return guess_category(name, default=None)


def guess_category(name: str, *, default: str | None = OTHER) -> str | None:
    """The category whose keywords appear in ``name``; ``default`` when none does."""
    folded = f" {fold(name)} "
    lowered = name.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        for keyword in keywords:
            if re.search(r"[㐀-鿿]", keyword):
                if keyword in lowered:
                    return category
            elif f" {keyword} " in folded:
                return category
    return default


def normalize_category(value: str | None) -> str:
    """An admin's category: a known one when it matches, else their own text (trimmed)."""
    text = " ".join(str(value or "").split())[:MAX_CATEGORY_CHARS]
    if not text:
        return OTHER
    return _canonical(text) or text


def title_and_category(file_name: str) -> tuple[str, str]:
    """A clean title and its category from an uploaded file's name.

    ``[Buồn] Mưa rơi_(Official Audio) 320kbps.mp3`` -> ("Mưa rơi", "Buồn / Cảm động").
    """
    stem = Path(file_name or "").stem
    explicit = None
    match = _EXPLICIT_PREFIX.match(stem)
    if match:
        explicit = normalize_category(match.group(1))
        stem = stem[match.end():]
    cleaned = _BITRATE.sub(" ", _NOISE.sub(" ", stem))
    cleaned = re.sub(r"[_]+", " ", cleaned)
    cleaned = re.sub(r"\s*-\s*$|^\s*-\s*", "", " ".join(cleaned.split()))
    title = cleaned.strip(" -.") or Path(file_name or "").stem.strip()
    return title, explicit or guess_category(stem) or OTHER
