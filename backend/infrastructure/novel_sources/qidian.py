"""Qidian (起点中文网) adapter.

Plain HTTP gets only an anti-bot ``probe.js`` shell (HTTP 202), so pages are read
in the shared source-login Chromium. Page cues verified on the VPS on 2026-11-04
(book 1045928363): volume headings ``<h3>…·共N章</h3>``, chapter links
``/chapter/{book}/{chapter}/`` whose ``title`` carries ``章节字数：N``, a lock icon
(``em.iconfont`` with glyph U+E63C) on chapters the session cannot read, chapter
text in ``span.content-text``, and ``需要订阅后才能阅读`` / ``本章为付费章节`` on a
locked chapter's preview.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlsplit

from infrastructure.novel_sources.base import (
    ACCESSIBLE,
    PURCHASE_REQUIRED,
    ChapterText,
    NovelCatalog,
    NovelChapter,
    NovelSourceError,
)
from infrastructure.novel_sources.browser import CdpPageRunner

_BOOK_PATTERNS = (
    re.compile(r"^/book/(\d+)"),
    re.compile(r"^/chapter/(\d+)/\d+"),
    re.compile(r"^/info/(\d+)"),  # book.qidian.com/info/<id>
)
_READY_TIMEOUT_SECONDS = 20.0
_POLL_SECONDS = 1.0

_CATALOG_SCRIPT = r"""
() => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const first = (selectors) => {
    for (const selector of selectors) {
      const text = clean(document.querySelector(selector)?.textContent);
      if (text) return text;
    }
    return '';
  };
  const title = first(['.book-info h1 em', '.book-info h1', 'h1'])
    || clean((document.title || '').split('_')[0].replace(/最新章节.*$/, ''));
  const author = first(['.book-info a.writer', '.book-info a[href*="/author/"]', 'a.writer']);
  const chapters = [];
  const seen = new Set();
  for (const heading of document.querySelectorAll('h3')) {
    const head = clean(heading.textContent);
    if (!/·\s*共\s*\d+\s*章/.test(head)) continue;
    const volume = clean(head.replace(/^订阅本卷\s*/, '').split('·')[0]);
    const vip = head.includes('VIP') && !head.includes('免费');
    for (let block = heading.nextElementSibling; block && block.tagName !== 'H3'; block = block.nextElementSibling) {
      for (const link of block.querySelectorAll('a[href*="/chapter/"]')) {
        const match = (link.getAttribute('href') || '').match(/\/chapter\/(\d+)\/(\d+)/);
        const name = clean(link.textContent);
        if (!match || !name || seen.has(match[2])) continue;
        seen.add(match[2]);
        const words = (link.getAttribute('title') || '').match(/章节字数[:：]\s*([\d,]+)/);
        let row = link.parentElement;
        while (row && row !== block && row.querySelectorAll('a[href*="/chapter/"]').length === 1
               && !row.querySelector('em.iconfont')) {
          row = row.parentElement;
        }
        const locked = !!row && row.querySelectorAll('a[href*="/chapter/"]').length === 1
          && [...row.querySelectorAll('em.iconfont')].some((icon) => clean(icon.textContent) === '');
        chapters.push({
          id: match[2],
          title: name,
          url: `https://www.qidian.com/chapter/${match[1]}/${match[2]}/`,
          volume,
          is_vip: vip,
          locked,
          word_count: words ? parseInt(words[1].replace(/,/g, ''), 10) : null,
        });
      }
    }
  }
  return { title, author, chapters };
}
"""

_CHAPTER_SCRIPT = r"""
() => {
  const body = document.body ? document.body.innerText : '';
  const preview = body.includes('VIP')
    && (body.includes('需要订阅后才能阅读') || body.includes('本章为付费章节'));
  const read = (nodes, strip) => [...nodes].map((node) => {
    const copy = strip ? node.cloneNode(true) : node;
    if (strip) copy.querySelectorAll('.review, .review-count, .review-icon').forEach((noise) => noise.remove());
    return (copy.textContent || '').trim();
  }).filter(Boolean);
  let paragraphs = read(document.querySelectorAll('span.content-text'), false);
  if (!paragraphs.length) {
    for (const selector of ['main p', '.read-content p', '.chapter-content p', '#j_chapterContent p']) {
      const nodes = document.querySelectorAll(selector);
      if (nodes.length) { paragraphs = read(nodes, true); break; }
    }
  }
  return { preview, paragraphs };
}
"""


class QidianSource:
    name = "qidian"
    label = "Qidian (起点)"

    def __init__(self, runner: CdpPageRunner) -> None:
        self._runner = runner

    @staticmethod
    def matches(url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return host == "qidian.com" or host.endswith(".qidian.com")

    def book_id(self, url: str) -> str | None:
        value = url.strip()
        if value.isdigit():
            return value
        if not self.matches(value):
            return None
        path = re.sub(r"/{2,}", "/", urlsplit(value).path or "/")
        for pattern in _BOOK_PATTERNS:
            match = pattern.match(path)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _evaluate(page: Any, script: str) -> Any:
        try:
            return page.evaluate(script)
        except Exception as exc:
            # probe.js answers the anti-bot check and then reloads the page, which
            # destroys the context of an evaluation already in flight.
            if "context was destroyed" in str(exc) or "navigat" in str(exc).lower():
                return None
            raise

    @classmethod
    def _wait(cls, page: Any, script: str, ready) -> Any:
        deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
        result = cls._evaluate(page, script)
        while not ready(result) and time.monotonic() < deadline:
            page.wait_for_timeout(int(_POLL_SECONDS * 1000))
            result = cls._evaluate(page, script)
        return result

    def fetch_catalog(self, book_id: str) -> NovelCatalog:
        if not str(book_id).isdigit():
            raise NovelSourceError("Mã truyện Qidian không hợp lệ")
        url = f"https://www.qidian.com/book/{book_id}/catalog/"

        def read(page: Any) -> dict[str, Any]:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            return self._wait(page, _CATALOG_SCRIPT, lambda data: bool(data and data.get("chapters")))

        data = self._runner.run(read)
        if not data or not data.get("chapters"):
            raise NovelSourceError(
                "Không đọc được mục lục Qidian (truyện không tồn tại, hoặc trang yêu cầu xác minh — "
                "mở Qidian ở trang Nguồn để kiểm tra)"
            )
        chapters = [
            NovelChapter(
                id=str(item["id"]), title=str(item["title"]), url=str(item["url"]),
                volume=str(item.get("volume") or ""), is_vip=bool(item.get("is_vip")),
                access=PURCHASE_REQUIRED if item.get("locked") else ACCESSIBLE,
                word_count=item.get("word_count"),
            )
            for item in data["chapters"]
        ]
        return NovelCatalog(
            source=self.name, book_id=str(book_id), title=str(data.get("title") or f"Qidian {book_id}"),
            author=str(data.get("author") or ""), url=f"https://www.qidian.com/book/{book_id}/",
            chapters=chapters,
        )

    def fetch_chapter(self, chapter: NovelChapter) -> ChapterText:
        if not self.matches(chapter.url) or not re.match(r"^/chapter/\d+/\d+/?$", urlsplit(chapter.url).path):
            raise NovelSourceError("Link chương Qidian không hợp lệ")

        def read(page: Any) -> dict[str, Any]:
            page.goto(chapter.url, wait_until="domcontentloaded", timeout=45_000)
            return self._wait(page, _CHAPTER_SCRIPT,
                              lambda data: bool(data and (data.get("paragraphs") or data.get("preview"))))

        data = self._runner.run(read) or {}
        return ChapterText(paragraphs=[str(p) for p in data.get("paragraphs") or []],
                           is_preview=bool(data.get("preview")))
