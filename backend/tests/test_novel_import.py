from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from application.novel_import_routes import create_novel_import_router
from infrastructure.novel_sources.base import (
    PURCHASE_REQUIRED,
    ChapterText,
    NovelCatalog,
    NovelChapter,
    NovelSourceError,
    is_protected_text,
    split_into_episodes,
)
from infrastructure.novel_sources.jjwxc import JjwxcSource, parse_chapter
from infrastructure.novel_sources.qidian import QidianSource
from infrastructure.novel_sources.service import NovelImportService
from domain.plan_entitlements import DEFAULT_PLAN_FEATURES, account_features


class InlineExecutor:
    """Runs submitted imports immediately, so tests see the finished state."""

    def submit(self, function, *args):
        future: Future = Future()
        future.set_result(function(*args))
        return future


class FakeSource:
    name = "fake"
    label = "Fake"

    def __init__(self, chapters: dict[str, ChapterText], catalog_chapters: list[NovelChapter]) -> None:
        self.chapters = chapters
        self.catalog_chapters = catalog_chapters
        self.catalog_calls = 0
        self.fetched: list[str] = []
        self.failures: dict[str, int] = {}

    def matches(self, url: str) -> bool:
        return url.startswith("https://fake.example/")

    def book_id(self, url: str) -> str | None:
        return url.rsplit("/", 1)[-1] if self.matches(url) else None

    def fetch_catalog(self, book_id: str) -> NovelCatalog:
        self.catalog_calls += 1
        return NovelCatalog("fake", book_id, "Truyện thử", "Tác giả", f"https://fake.example/{book_id}",
                            list(self.catalog_chapters))

    def fetch_chapter(self, chapter: NovelChapter) -> ChapterText:
        self.fetched.append(chapter.id)
        if self.failures.get(chapter.id, 0) > 0:
            self.failures[chapter.id] -= 1
            raise TimeoutError("page timeout")
        return self.chapters[chapter.id]


def chapter(chapter_id: str, words: int = 20, **fields) -> NovelChapter:
    return NovelChapter(id=chapter_id, title=f"第{chapter_id}章", url=f"https://fake.example/c/{chapter_id}",
                        word_count=words, **fields)


class RulesTests(unittest.TestCase):
    def test_protected_text_is_detected_by_glyphs_or_missing_length(self) -> None:
        self.assertFalse(is_protected_text("相原正在警署给二叔办手续。" * 10, 130))
        self.assertTrue(is_protected_text("正文" * 10, None))
        self.assertTrue(is_protected_text("只有一点点", 4000))
        self.assertFalse(is_protected_text("", 4000))

    def test_episodes_keep_chapters_whole_and_respect_the_limit(self) -> None:
        blocks = [(f"第{i}章", f"第{i}章\n\n" + "字" * 40) for i in range(1, 6)]  # 45 chars each
        episodes = split_into_episodes(blocks, 100)
        self.assertEqual([episode["chapters"] for episode in episodes],
                         [["第1章", "第2章"], ["第3章", "第4章"], ["第5章"]])
        self.assertTrue(all(episode["chars"] <= 100 for episode in episodes))
        self.assertEqual([episode["number"] for episode in episodes], [1, 2, 3])

    def test_an_over_long_chapter_is_cut_at_sentence_ends(self) -> None:
        block = "第1章\n\n" + "他说：“走吧。”" * 30
        episodes = split_into_episodes([("第1章", block)], 60)
        self.assertGreater(len(episodes), 1)
        self.assertTrue(all(episode["chars"] <= 60 for episode in episodes))
        self.assertEqual("".join(episode["text"] for episode in episodes).replace("\n", ""),
                         block.replace("\n", ""), "no text is lost, closing quotes included")


class QidianLinkTests(unittest.TestCase):
    def test_book_links_and_ids(self) -> None:
        source = QidianSource(runner=None)
        for url in (
            "https://www.qidian.com/book/1045928363/",
            "https://www.qidian.com/book/1045928363/catalog/",
            "https://www.qidian.com/chapter/1045928363/803251225/",
            "https://book.qidian.com/info/1045928363/",
            "https://m.qidian.com/book/1045928363",
            "1045928363",
        ):
            self.assertEqual(source.book_id(url), "1045928363", url)
        self.assertIsNone(source.book_id("https://www.qidian.com/rank/"))
        self.assertIsNone(source.book_id("https://evil.example/book/1045928363"))

    def test_chapter_urls_outside_qidian_are_refused(self) -> None:
        source = QidianSource(runner=None)
        with self.assertRaises(NovelSourceError):
            source.fetch_chapter(chapter("1"))


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Path(directory.name)
        self.sleeps: list[float] = []
        self.text = "相原正在警署给二叔办手续。"  # 13 chars, catalog says 20 words: readable.
        self.source = FakeSource(
            {
                "1": ChapterText([self.text, self.text]),
                "2": ChapterText([self.text]),
                "3": ChapterText(["雾山的迷雾深处。"], is_preview=True),
                "4": ChapterText([""]),
            },
            [chapter("1"), chapter("2"), chapter("3", is_vip=True, access=PURCHASE_REQUIRED), chapter("4")],
        )

    def service(self, **options) -> NovelImportService:
        return NovelImportService([self.source], self.cache, episode_chars=options.pop("episode_chars", 1000),
                                  sleep=self.sleeps.append, executor=InlineExecutor(), **options)

    def test_import_keeps_readable_chapters_and_explains_the_rest(self) -> None:
        state = self.service().start_import("u1", "https://fake.example/42", ["4", "3", "2", "1"])
        self.assertEqual(state["status"], "completed")
        self.assertEqual([(c["id"], c["status"]) for c in state["chapters"]],
                         [("1", "ok"), ("2", "ok"), ("3", "preview"), ("4", "protected")])
        self.assertEqual(len(state["episodes"]), 1)
        self.assertEqual(state["episodes"][0]["chapters"], ["第1章", "第2章"])
        self.assertTrue(state["episodes"][0]["text"].startswith("第1章\n\n" + self.text))

    def test_fetches_are_paced_and_cached_chapters_are_not_fetched_again(self) -> None:
        service = self.service()
        service.start_import("u1", "https://fake.example/42", ["1", "2"])
        self.assertEqual(self.source.fetched, ["1", "2"])
        self.assertEqual(len(self.sleeps), 1, "one pause between two network fetches")
        self.assertTrue(5 <= self.sleeps[0] <= 12)
        self.source.fetched.clear()
        self.sleeps.clear()
        service.start_import("u1", "https://fake.example/42", ["1", "2"])
        self.assertEqual(self.source.fetched, [])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self.source.catalog_calls, 1, "the catalog is cached too")
        self.assertEqual(service.cached_chapter_ids(service.catalog("https://fake.example/42")), {"1", "2"})

    def test_transient_failures_are_retried(self) -> None:
        self.source.failures["1"] = 2
        state = self.service().start_import("u1", "https://fake.example/42", ["1"])
        self.assertEqual(state["chapters"][0]["status"], "ok")
        self.assertEqual(self.source.fetched, ["1", "1", "1"])

    def test_many_chapters_become_several_episodes(self) -> None:
        state = self.service(episode_chars=40).start_import("u1", "https://fake.example/42", ["1", "2"])
        self.assertEqual([episode["chapters"] for episode in state["episodes"]], [["第1章"], ["第2章"]])

    def test_imports_are_private_to_their_owner(self) -> None:
        service = self.service()
        state = service.start_import("u1", "https://fake.example/42", ["1"])
        with self.assertRaises(KeyError):
            service.status("u2", state["import_id"])
        self.assertNotIn("text", service.status("u1", state["import_id"], include_text=False)["episodes"][0])

    def test_unknown_links_and_chapters_are_refused(self) -> None:
        service = self.service()
        with self.assertRaisesRegex(NovelSourceError, "chưa được hỗ trợ"):
            service.start_import("u1", "https://other.example/1", ["1"])
        with self.assertRaisesRegex(NovelSourceError, "mục lục"):
            service.start_import("u1", "https://fake.example/42", ["99"])


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        source = FakeSource({"1": ChapterText(["相原正在警署给二叔办手续。" * 2])}, [chapter("1")])
        service = NovelImportService([source], Path(directory.name), episode_chars=1000,
                                     sleep=lambda _: None, executor=InlineExecutor())
        self.role = "editor"

        def require_roles(request: Request, *roles: str):
            if self.role not in roles:
                raise HTTPException(403, "forbidden")
            return {"user_id": "u1", "role": self.role}

        self.allowed = True

        def ensure_allowed(account):
            if not self.allowed:
                raise HTTPException(403, "Nhập chương truyện từ link chỉ dành cho gói trả phí")

        app = FastAPI()
        app.include_router(create_novel_import_router(service, require_roles, ensure_allowed))
        self.client = TestClient(app)

    def test_editor_imports_after_confirming_rights(self) -> None:
        catalog = self.client.get("/api/v1/comic/sources/catalog", params={"url": "https://fake.example/42"})
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.json()["chapters"][0]["cached"], False)
        refused = self.client.post("/api/v1/comic/sources/imports",
                                   json={"url": "https://fake.example/42", "chapter_ids": ["1"],
                                         "rights_confirmed": False})
        self.assertEqual(refused.status_code, 422)
        started = self.client.post("/api/v1/comic/sources/imports",
                                   json={"url": "https://fake.example/42", "chapter_ids": ["1"],
                                         "rights_confirmed": True})
        self.assertEqual(started.status_code, 202)
        import_id = started.json()["import_id"]
        done = self.client.get(f"/api/v1/comic/sources/imports/{import_id}", params={"include_text": True})
        self.assertEqual(done.json()["status"], "completed")
        self.assertIn("相原", done.json()["episodes"][0]["text"])

    def test_plan_without_novel_import_is_refused_even_for_catalog(self) -> None:
        self.allowed = False
        catalog = self.client.get("/api/v1/comic/sources/catalog", params={"url": "https://fake.example/42"})
        self.assertEqual(catalog.status_code, 403)
        started = self.client.post("/api/v1/comic/sources/imports",
                                   json={"url": "https://fake.example/42", "chapter_ids": ["1"],
                                         "rights_confirmed": True})
        self.assertEqual(started.status_code, 403)

    def test_viewer_is_refused(self) -> None:
        self.role = "viewer"
        response = self.client.get("/api/v1/comic/sources/catalog", params={"url": "https://fake.example/42"})
        self.assertEqual(response.status_code, 403)

    def test_unsupported_link_is_a_clear_error(self) -> None:
        response = self.client.get("/api/v1/comic/sources/catalog", params={"url": "https://other.example/1"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("chưa được hỗ trợ", response.json()["detail"])


# Trimmed from https://www.jjwxc.net/onebook.php?novelid=10841540 (2026-11-04).
JJWXC_BOOK = """<title>《滂沱雨[娱乐圈]》江好桃_晋江文学城_【原创小说|言情小说】</title>
<h1 itemprop="name"><span itemprop="articleSection">滂沱雨[娱乐圈]</span> </h1>
<tr itemprop="chapter" itemscope itemtype="http://schema.org/Chapter"> <td align="center"> 1 </td> <td>
<span itemprop="headline"> <div style="float:left"> <a itemprop="url"
href="http://www.jjwxc.net/onebook.php?novelid=10841540&chapterid=1">Chapter 1</a> </div> </span> </td>
<td> 似我者死 </td> <td align="center" itemprop="wordCount">4380</td> </tr>
<tr itemprop="chapter" itemscope itemtype="http://schema.org/Chapter"> <td align="center"> 16 </td> <td>
<span itemprop="headline"> <div style="float:left"> <a itemprop="url" style="cursor:pointer" onclick="vip_buy('vip_16')"
rel="http://my.jjwxc.net/onebook_vip.php?novelid=10841540&chapterid=16" id="vip_16" >Chapter 16</a><a
style="cursor:pointer"><font color="red">[VIP]</font></a> </div> </span> </td>
<td> 只争朝夕 </td> <td colspan="2" align="center" itemprop="wordCount">3195</td> </tr>"""
JJWXC_CHAPTER = """<div id="paragraph_comment_content">
\u3000\u3000“春姐，你的航班还没起飞吗？”<br><br>\u3000\u3000夏日的傍晚，本还应该明亮的上海却陷进滂沱大雨里。<br><br>
\u3000\u3000“抱、抱歉，贺老师……”</div>"""


class JjwxcTests(unittest.TestCase):
    def test_links_and_ids(self) -> None:
        source = JjwxcSource(fetch=lambda url: "")
        for url in ("https://www.jjwxc.net/onebook.php?novelid=10841540&chapterid=1",
                    "http://www.jjwxc.net/onebook.php?novelid=10841540",
                    "https://m.jjwxc.net/book2/10841540"):
            self.assertEqual(source.book_id(url), "10841540", url)
        self.assertIsNone(source.book_id("https://www.jjwxc.net/"))
        self.assertIsNone(source.book_id("https://evil.example/onebook.php?novelid=1"))

    def test_catalog_marks_vip_chapters(self) -> None:
        requested = []
        source = JjwxcSource(fetch=lambda url: requested.append(url) or JJWXC_BOOK)
        catalog = source.fetch_catalog("10841540")
        self.assertEqual((catalog.title, catalog.author), ("滂沱雨[娱乐圈]", "江好桃"))
        self.assertEqual([(c.id, c.title, c.access, c.word_count) for c in catalog.chapters], [
            ("1", "Chapter 1 似我者死", "accessible", 4380),
            ("16", "Chapter 16 只争朝夕", "purchase_required", 3195),
        ])
        self.assertEqual(requested, ["https://www.jjwxc.net/onebook.php?novelid=10841540"])

    def test_chapter_paragraphs_and_vip_without_request(self) -> None:
        self.assertEqual(parse_chapter(JJWXC_CHAPTER), [
            "“春姐，你的航班还没起飞吗？”", "夏日的傍晚，本还应该明亮的上海却陷进滂沱大雨里。", "“抱、抱歉，贺老师……”",
        ])
        requested = []
        source = JjwxcSource(fetch=lambda url: requested.append(url) or JJWXC_BOOK)
        vip = source.fetch_catalog("10841540").chapters[1]
        requested.clear()
        self.assertTrue(source.fetch_chapter(vip).is_preview)
        self.assertEqual(requested, [], "a paid chapter is never requested")


class NovelImportEntitlementTests(unittest.TestCase):
    def test_paid_plans_get_it_by_default_and_free_plans_do_not(self) -> None:
        for plan in ("creator", "pro", "enterprise"):
            self.assertIn("novel_import", DEFAULT_PLAN_FEATURES[plan], plan)
        for plan in ("trial", "free"):
            self.assertNotIn("novel_import", DEFAULT_PLAN_FEATURES[plan], plan)
        self.assertIn("novel_import", account_features({"role": "admin", "plan": "free"}))


if __name__ == "__main__":
    unittest.main()
