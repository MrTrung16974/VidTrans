import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.seo_routes import PLACEHOLDER, create_seo_router

FRONTEND = Path(__file__).resolve().parents[2] / "web"


class SeoRouteTests(unittest.TestCase):
    def client(self, directory: Path = FRONTEND) -> TestClient:
        app = FastAPI()
        app.include_router(create_seo_router(directory))
        return TestClient(app)

    def test_index_gets_the_configured_public_url(self) -> None:
        with patch.dict("os.environ", {"VIDTRANS_PUBLIC_BASE_URL": "https://vidtrans.example.vn/"}):
            html = self.client().get("/").text
        self.assertNotIn(PLACEHOLDER, html)
        self.assertIn('<link rel="canonical" href="https://vidtrans.example.vn/" />', html)
        self.assertIn('content="https://vidtrans.example.vn/assets/og-cover.png"', html)

    def test_forwarded_host_is_used_when_unconfigured_and_bad_hosts_are_ignored(self) -> None:
        with patch.dict("os.environ", {"VIDTRANS_PUBLIC_BASE_URL": ""}):
            client = self.client()
            html = client.get("/", headers={"x-forwarded-host": "studio.example.com",
                                            "x-forwarded-proto": "https"}).text
            self.assertIn('href="https://studio.example.com/"', html)
            evil = client.get("/", headers={"host": 'x"><script>alert(1)</script>'}).text
            self.assertNotIn("<script>alert(1)", evil)

    def test_robots_and_sitemap(self) -> None:
        with patch.dict("os.environ", {"VIDTRANS_PUBLIC_BASE_URL": "https://vidtrans.example.vn"}):
            client = self.client()
            robots = client.get("/robots.txt").text
            sitemap = client.get("/sitemap.xml")
        self.assertIn("Disallow: /api/", robots)
        self.assertIn("Sitemap: https://vidtrans.example.vn/sitemap.xml", robots)
        self.assertEqual(sitemap.headers["content-type"], "application/xml")
        self.assertIn("<loc>https://vidtrans.example.vn/</loc>", sitemap.text)

    def test_index_edits_are_picked_up_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            index = Path(temp) / "index.html"
            index.write_text(f"<a href='{PLACEHOLDER}/'>v1</a>", encoding="utf-8")
            client = self.client(Path(temp))
            self.assertIn("v1", client.get("/").text)
            index.write_text("v2", encoding="utf-8")
            import os
            os.utime(index, (1, 2))
            self.assertEqual(client.get("/").text, "v2")

    def test_shipped_page_targets_vietnamese_search_terms(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        title = html.split("<title>")[1].split("</title>")[0]
        self.assertIn("Dịch video tiếng Trung sang tiếng Việt", title)
        for term in ("lồng tiếng AI", "phụ đề tự động", "dịch video Douyin", "application/ld+json", 'property="og:image"'):
            self.assertIn(term, html)
        self.assertTrue((FRONTEND / "assets" / "og-cover.png").is_file())


if __name__ == "__main__":
    unittest.main()
