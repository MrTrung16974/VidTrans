"""Jinjiang (晋江文学城, jjwxc.net) adapter.

Unlike Qidian, plain HTTP gets the real pages (GB18030), from the VPS too, so no
browser is needed. Page cues verified on 2026-11-04 (novel 10841540): chapter rows
``<tr itemprop="chapter">`` with the number in the first cell, a link to
``onebook.php?novelid=…&chapterid=N`` for free chapters or a ``rel`` to
``my.jjwxc.net/onebook_vip.php`` plus a ``[VIP]`` mark for paid ones, a summary
cell and ``itemprop="wordCount"``; chapter text in ``#paragraph_comment_content``
with ``<br>``-separated paragraphs.

VIP chapters need a purchase and are not fetched: they come back as previews.
"""

from __future__ import annotations

import html
import re
import urllib.request
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from infrastructure.novel_sources.base import (
    ACCESSIBLE,
    PURCHASE_REQUIRED,
    ChapterText,
    NovelCatalog,
    NovelChapter,
    NovelSourceError,
)

_HOSTS = ("jjwxc.net",)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)
_ROW = re.compile(r'<tr[^>]*itemprop="chapter"[^>]*>(.*?)</tr>', re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_FREE_LINK = re.compile(r'href="[^"]*onebook\.php\?novelid=(\d+)&(?:amp;)?chapterid=(\d+)"[^>]*>(.*?)</a>', re.S)
_VIP_LINK = re.compile(r'rel="[^"]*onebook_vip\.php\?novelid=(\d+)&(?:amp;)?chapterid=(\d+)"[^>]*>(.*?)</a>', re.S)
_WORDS = re.compile(r'itemprop="wordCount"[^>]*>\s*(\d+)')
_TAGS = re.compile(r"<[^>]+>")
_BODY = re.compile(r'<div id="paragraph_comment_content">(.*?)</div>', re.S)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub("", fragment))).strip()


def _http_get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read().decode("gb18030", errors="replace")


def parse_catalog(page: str, book_id: str) -> NovelCatalog:
    title_match = re.search(r'<span itemprop="articleSection">(.*?)</span>', page, re.S)
    head = re.search(r"<title>\s*《(.*?)》(.*?)_晋江文学城", page, re.S)
    title = _text(title_match.group(1)) if title_match else (_text(head.group(1)) if head else f"Jinjiang {book_id}")
    author = _text(head.group(2)) if head else ""
    chapters: list[NovelChapter] = []
    for row in _ROW.findall(page):
        cells = _CELL.findall(row)
        free = _FREE_LINK.search(row)
        vip = None if free else _VIP_LINK.search(row)
        link = free or vip
        if not link or link.group(1) != book_id:
            continue  # Removed or locked chapters have no link.
        number = link.group(2)
        name = _text(link.group(3))
        summary = _text(cells[2]) if len(cells) > 2 else ""
        words = _WORDS.search(row)
        chapters.append(NovelChapter(
            id=number,
            title=f"{name} {summary}".strip() if summary and summary not in name else name,
            url=(f"https://www.jjwxc.net/onebook.php?novelid={book_id}&chapterid={number}" if free
                 else f"https://my.jjwxc.net/onebook_vip.php?novelid={book_id}&chapterid={number}"),
            is_vip=vip is not None,
            access=PURCHASE_REQUIRED if vip else ACCESSIBLE,
            word_count=int(words.group(1)) if words else None,
        ))
    return NovelCatalog(source="jjwxc", book_id=book_id, title=title, author=author,
                        url=f"https://www.jjwxc.net/onebook.php?novelid={book_id}", chapters=chapters)


def parse_chapter(page: str) -> list[str]:
    match = _BODY.search(page)
    if not match:
        return []
    paragraphs = [_text(part).strip("　 ") for part in re.split(r"<br\s*/?>", match.group(1))]
    return [paragraph for paragraph in paragraphs if paragraph]


class JjwxcSource:
    name = "jjwxc"
    label = "Jinjiang (晋江)"

    def __init__(self, fetch: Callable[[str], str] = _http_get) -> None:
        self._fetch = fetch

    @staticmethod
    def matches(url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return any(host == domain or host.endswith("." + domain) for domain in _HOSTS)

    def book_id(self, url: str) -> str | None:
        if not self.matches(url):
            return None
        parsed = urlsplit(url)
        value = (parse_qs(parsed.query).get("novelid") or [""])[0]
        if value.isdigit():
            return value
        match = re.match(r"^/book2/(\d+)", parsed.path or "")  # Mobile site.
        return match.group(1) if match else None

    def fetch_catalog(self, book_id: str) -> NovelCatalog:
        if not str(book_id).isdigit():
            raise NovelSourceError("Mã truyện Jinjiang không hợp lệ")
        try:
            page = self._fetch(f"https://www.jjwxc.net/onebook.php?novelid={book_id}")
        except OSError as exc:
            raise NovelSourceError("Không mở được trang truyện Jinjiang") from exc
        catalog = parse_catalog(page, str(book_id))
        if not catalog.chapters:
            raise NovelSourceError("Không đọc được mục lục Jinjiang (truyện không tồn tại hoặc đã bị khóa)")
        return catalog

    def fetch_chapter(self, chapter: NovelChapter) -> ChapterText:
        if chapter.access != ACCESSIBLE:
            # Paid chapters need a purchase; nothing is requested for them.
            return ChapterText(paragraphs=[], is_preview=True)
        parsed = urlsplit(chapter.url)
        query = parse_qs(parsed.query)
        if (parsed.hostname or "").lower() != "www.jjwxc.net" or parsed.path != "/onebook.php" \
                or not (query.get("novelid") or [""])[0].isdigit() or not (query.get("chapterid") or [""])[0].isdigit():
            raise NovelSourceError("Link chương Jinjiang không hợp lệ")
        # Network errors propagate so the import service retries them.
        return ChapterText(paragraphs=parse_chapter(self._fetch(chapter.url)))
