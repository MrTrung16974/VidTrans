"""Catalog lookups and background chapter imports, with a disk cache and polite pacing."""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Sequence

from infrastructure.novel_sources.base import (
    ACCESSIBLE,
    CHAPTER_FAILED,
    CHAPTER_OK,
    CHAPTER_PREVIEW,
    CHAPTER_PROTECTED,
    ChapterText,
    NovelCatalog,
    NovelChapter,
    NovelSource,
    NovelSourceError,
    chapter_block,
    is_protected_text,
    split_into_episodes,
)

logger = logging.getLogger(__name__)

CATALOG_TTL_SECONDS = 24 * 3600
MAX_IMPORT_CHAPTERS = 200
IMPORT_RETENTION_SECONDS = 3600
_SAFE_ID = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-")


def _safe(value: str) -> str:
    if not value or any(char not in _SAFE_ID for char in value):
        raise NovelSourceError("Mã truyện hoặc chương không hợp lệ")
    return value


class NovelImportService:
    def __init__(
        self,
        sources: Sequence[NovelSource],
        cache_dir: Path,
        *,
        episode_chars: int,
        min_delay: float = 5.0,
        max_delay: float = 12.0,
        retries: int = 3,
        retry_base_delay: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._sources = list(sources)
        self._cache_dir = cache_dir
        self.episode_chars = episode_chars
        self._min_delay, self._max_delay = min_delay, max_delay
        self._retries, self._retry_base_delay = retries, retry_base_delay
        self._sleep, self._clock = sleep, clock
        # One worker: imports share one browser and must not hammer a site in parallel.
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="vidtrans-novel-import")
        self._imports: dict[str, dict[str, Any]] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ sources
    def supported_sources(self) -> list[dict[str, str]]:
        return [{"name": source.name, "label": source.label} for source in self._sources]

    def resolve(self, url: str) -> tuple[NovelSource, str]:
        for source in self._sources:
            book_id = source.book_id(url)
            if book_id:
                return source, _safe(book_id)
        labels = ", ".join(source.label for source in self._sources)
        raise NovelSourceError(f"Link chưa được hỗ trợ. Hiện hỗ trợ: {labels}")

    # ------------------------------------------------------------------ cache
    def _book_dir(self, source: NovelSource, book_id: str) -> Path:
        return self._cache_dir / _safe(source.name) / _safe(book_id)

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def catalog(self, url: str, *, refresh: bool = False) -> NovelCatalog:
        source, book_id = self.resolve(url)
        path = self._book_dir(source, book_id) / "catalog.json"
        cached = None if refresh else self._read_json(path)
        if cached and self._clock() - float(cached.get("fetched_at", 0)) < CATALOG_TTL_SECONDS:
            return NovelCatalog.from_dict(cached["catalog"])
        catalog = source.fetch_catalog(book_id)
        self._write_json(path, {"fetched_at": self._clock(), "catalog": catalog.as_dict()})
        return catalog

    def cached_chapter_ids(self, catalog: NovelCatalog) -> set[str]:
        source = self._source(catalog.source)
        folder = self._book_dir(source, catalog.book_id) / "chapters"
        cached = set()
        for chapter in catalog.chapters:
            entry = self._read_json(folder / f"{_safe(chapter.id)}.json")
            if entry and entry.get("word_count") == chapter.word_count:
                cached.add(chapter.id)
        return cached

    def _source(self, name: str) -> NovelSource:
        for source in self._sources:
            if source.name == name:
                return source
        raise NovelSourceError("Nguồn truyện không còn được hỗ trợ")

    # ------------------------------------------------------------------ imports
    def start_import(self, owner: str, url: str, chapter_ids: Sequence[str]) -> dict[str, Any]:
        catalog = self.catalog(url)
        wanted = list(dict.fromkeys(str(chapter_id) for chapter_id in chapter_ids))
        if not wanted:
            raise NovelSourceError("Hãy chọn ít nhất một chương")
        if len(wanted) > MAX_IMPORT_CHAPTERS:
            raise NovelSourceError(f"Mỗi lần chỉ tải tối đa {MAX_IMPORT_CHAPTERS} chương")
        by_id = {chapter.id: chapter for chapter in catalog.chapters}
        missing = [chapter_id for chapter_id in wanted if chapter_id not in by_id]
        if missing:
            raise NovelSourceError("Có chương không còn trong mục lục; hãy tải lại mục lục")
        # Catalog order, whatever order the client sent.
        chapters = [chapter for chapter in catalog.chapters if chapter.id in set(wanted)]
        import_id = uuid.uuid4().hex
        state = {
            "import_id": import_id, "owner": owner, "status": "queued",
            "source": catalog.source, "book_id": catalog.book_id, "title": catalog.title, "author": catalog.author,
            "done": 0, "total": len(chapters),
            "chapters": [{"id": c.id, "title": c.title, "status": "pending", "reason": ""} for c in chapters],
            "episodes": [], "error": "", "created_at": self._clock(), "finished_at": None,
        }
        with self._lock:
            self._prune()
            self._imports[import_id] = state
        self._executor.submit(self._run, import_id, catalog, chapters)
        return self.status(owner, import_id)

    def status(self, owner: str, import_id: str, *, include_text: bool = True) -> dict[str, Any]:
        with self._lock:
            state = self._imports.get(import_id)
            if state is None or state["owner"] != owner:
                raise KeyError(import_id)
            snapshot = json.loads(json.dumps(state))
        snapshot.pop("owner", None)
        if not include_text:
            for episode in snapshot["episodes"]:
                episode.pop("text", None)
        return snapshot

    def cancel(self, owner: str, import_id: str) -> dict[str, Any]:
        self.status(owner, import_id)  # Ownership check.
        with self._lock:
            self._cancelled.add(import_id)
            state = self._imports[import_id]
            if state["status"] == "queued":
                state["status"] = "cancelled"
        return self.status(owner, import_id)

    def _prune(self) -> None:
        now = self._clock()
        for import_id, state in list(self._imports.items()):
            finished = state.get("finished_at")
            if finished and now - finished > IMPORT_RETENTION_SECONDS:
                self._imports.pop(import_id, None)
                self._cancelled.discard(import_id)

    def _update(self, import_id: str, **fields: Any) -> None:
        with self._lock:
            self._imports[import_id].update(fields)

    def _set_chapter(self, import_id: str, index: int, status: str, reason: str = "") -> None:
        with self._lock:
            state = self._imports[import_id]
            state["chapters"][index].update(status=status, reason=reason)
            state["done"] = sum(1 for chapter in state["chapters"] if chapter["status"] != "pending")

    def _fetch_with_retries(self, source: NovelSource, chapter: NovelChapter, import_id: str) -> ChapterText:
        attempt = 0
        while True:
            try:
                return source.fetch_chapter(chapter)
            except NovelSourceError:
                raise
            except Exception as exc:
                attempt += 1
                if attempt > self._retries or import_id in self._cancelled:
                    raise NovelSourceError(f"Tải chương thất bại sau {attempt} lần thử") from exc
                logger.warning("Novel chapter %s failed (%s); retry %d", chapter.id, type(exc).__name__, attempt)
                self._sleep(self._retry_base_delay * 2 ** (attempt - 1))

    def _run(self, import_id: str, catalog: NovelCatalog, chapters: list[NovelChapter]) -> None:
        if import_id in self._cancelled:
            self._update(import_id, status="cancelled", finished_at=self._clock())
            return
        self._update(import_id, status="running")
        source = self._source(catalog.source)
        folder = self._book_dir(source, catalog.book_id) / "chapters"
        blocks: list[tuple[str, str]] = []
        fetched_before = False
        try:
            for index, chapter in enumerate(chapters):
                if import_id in self._cancelled:
                    break
                cache_path = folder / f"{_safe(chapter.id)}.json"
                cached = self._read_json(cache_path)
                if cached and cached.get("word_count") == chapter.word_count and cached.get("paragraphs"):
                    text = ChapterText(paragraphs=list(cached["paragraphs"]))
                else:
                    if fetched_before:
                        # Paced like a reader, never a burst against the site.
                        self._sleep(random.uniform(self._min_delay, self._max_delay))
                        if import_id in self._cancelled:
                            break
                    fetched_before = True
                    try:
                        text = self._fetch_with_retries(source, chapter, import_id)
                    except NovelSourceError as exc:
                        self._set_chapter(import_id, index, CHAPTER_FAILED, str(exc))
                        continue
                if text.is_preview or (chapter.access != ACCESSIBLE and not text.paragraphs):
                    self._set_chapter(import_id, index, CHAPTER_PREVIEW,
                                      "Chương VIP chưa mua: chỉ có phần xem trước, không đưa vào truyện")
                    continue
                if is_protected_text(text.text, chapter.word_count):
                    self._set_chapter(import_id, index, CHAPTER_PROTECTED,
                                      "Trang dùng chữ được bảo vệ/mã hóa nên không lấy được nội dung")
                    continue
                if not text.text:
                    self._set_chapter(import_id, index, CHAPTER_FAILED, "Trang chương không có nội dung")
                    continue
                if not cached or cached.get("paragraphs") != text.paragraphs:
                    self._write_json(cache_path, {"word_count": chapter.word_count, "paragraphs": text.paragraphs,
                                                  "fetched_at": self._clock()})
                blocks.append((chapter.title, chapter_block(chapter.title, text.text)))
                self._set_chapter(import_id, index, CHAPTER_OK)
        except Exception as exc:  # Never leave an import "running" forever.
            logger.exception("Novel import %s failed", import_id)
            self._update(import_id, status="failed", error=str(exc) or type(exc).__name__,
                         finished_at=self._clock())
            return
        episodes = split_into_episodes(blocks, self.episode_chars)
        status = "cancelled" if import_id in self._cancelled else ("completed" if blocks else "failed")
        error = "" if blocks else "Không lấy được chương nào"
        self._update(import_id, status=status, episodes=episodes, error=error if status == "failed" else "",
                     finished_at=self._clock())
