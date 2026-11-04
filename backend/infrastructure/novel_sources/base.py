"""Shared models and rules for importing novel chapters from web sources.

Only text the source shows to the current browser session is imported: free
chapters, chapters the logged-in account bought, and nothing else. Locked
chapters keep their public preview flagged, and text the site protects (custom
font obfuscation, anti-copy glyphs) is reported, never decoded.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

ACCESSIBLE = "accessible"
PURCHASE_REQUIRED = "purchase_required"

# Chapter outcomes of an import.
CHAPTER_OK = "ok"
CHAPTER_PREVIEW = "preview"
CHAPTER_PROTECTED = "protected"
CHAPTER_FAILED = "failed"

# Font-obfuscated pages render their glyphs from the Private Use Area.
_PRIVATE_USE = re.compile(r"[-]")
PROTECTED_PRIVATE_USE_SHARE = 0.02
# A readable chapter is never this much shorter than the length the catalog states.
PROTECTED_MIN_LENGTH_SHARE = 0.35
_SENTENCE_END = re.compile(r"(?<=[.!?…。！？])[\"'”’»)\]」』]*\s*")


class NovelSourceError(RuntimeError):
    """A user-facing failure while reading a novel source."""


@dataclass
class NovelChapter:
    id: str
    title: str
    url: str
    volume: str = ""
    is_vip: bool = False
    access: str = ACCESSIBLE
    word_count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NovelCatalog:
    source: str
    book_id: str
    title: str
    author: str
    url: str
    chapters: list[NovelChapter] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "chapters": [chapter.as_dict() for chapter in self.chapters]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NovelCatalog":
        chapters = [NovelChapter(**chapter) for chapter in data.get("chapters", [])]
        return cls(**{**{key: data[key] for key in ("source", "book_id", "title", "author", "url")},
                      "chapters": chapters})


@dataclass
class ChapterText:
    paragraphs: list[str]
    is_preview: bool = False

    @property
    def text(self) -> str:
        return "\n\n".join(paragraph.strip() for paragraph in self.paragraphs if paragraph.strip())


class NovelSource(Protocol):
    name: str
    label: str

    def matches(self, url: str) -> bool: ...
    def book_id(self, url: str) -> str | None: ...
    def fetch_catalog(self, book_id: str) -> NovelCatalog: ...
    def fetch_chapter(self, chapter: NovelChapter) -> ChapterText: ...


def is_protected_text(text: str, word_count: int | None) -> bool:
    """True when the page text is obfuscated rather than readable prose."""
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    if len(_PRIVATE_USE.findall(compact)) / len(compact) > PROTECTED_PRIVATE_USE_SHARE:
        return True
    return bool(word_count) and len(compact) < PROTECTED_MIN_LENGTH_SHARE * int(word_count)


def chapter_block(title: str, text: str) -> str:
    return f"{title.strip()}\n\n{text.strip()}"


def _split_long_text(block: str, limit: int) -> list[str]:
    """Cut one over-long chapter at sentence ends, never mid-sentence when avoidable."""
    cuts = sorted({match.end() for match in _SENTENCE_END.finditer(block)} | {len(block)})
    sentences, start = [], 0
    for cut in cuts:
        if cut > start:
            sentences.append(block[start:cut])
            start = cut
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > limit:  # A single sentence longer than the limit.
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if len(current) + len(sentence) > limit:
            pieces.append(current)
            current = ""
        current += sentence
    if current.strip():
        pieces.append(current)
    return [piece.strip() for piece in pieces if piece.strip()]


def split_into_episodes(blocks: list[tuple[str, str]], limit: int) -> list[dict[str, Any]]:
    """Group consecutive chapter blocks into episodes of at most ``limit`` characters.

    ``blocks`` are (chapter title, formatted block). Chapters are never split across
    episodes unless one chapter alone exceeds the limit; it is then cut at sentences.
    """
    separator = "\n\n"
    episodes: list[dict[str, Any]] = []
    current: list[str] = []
    titles: list[str] = []

    def flush() -> None:
        if current:
            episodes.append({"chapters": list(titles), "text": separator.join(current)})
            current.clear()
            titles.clear()

    for title, block in blocks:
        if len(block) > limit:
            flush()
            for index, piece in enumerate(_split_long_text(block, limit), 1):
                episodes.append({"chapters": [f"{title} ({index})"], "text": piece})
            continue
        size = sum(len(part) for part in current) + len(separator) * len(current)
        if current and size + len(block) > limit:
            flush()
        current.append(block)
        titles.append(title)
    flush()
    for number, episode in enumerate(episodes, 1):
        episode["number"] = number
        episode["chars"] = len(episode["text"])
    return episodes
