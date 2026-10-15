import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from starlette.applications import Starlette
from starlette.routing import Mount

from infrastructure.frontend_static import FrontendStaticFiles


class FrontendStaticTests(unittest.IsolatedAsyncioTestCase):
    async def test_instagram_publishing_controls_and_module_are_shipped(self):
        frontend = Path(__file__).resolve().parents[1] / "frontend"
        html = (frontend / "index.html").read_text(encoding="utf-8")
        javascript = (frontend / "js" / "instagram.js").read_text(encoding="utf-8")

        for element_id in (
            "instagramConnect", "instagramAccount", "instagramPublishMode",
            "instagramScheduleAt", "instagramReviewConsent", "instagramPublish",
            "instagramPublishStatus",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("/api/v1/instagram-auth/connect", javascript)
        self.assertIn("/social-publishes", javascript)
        self.assertIn("platform: 'INSTAGRAM'", javascript)

    async def test_facebook_and_youtube_publishing_controls_are_shipped(self):
        backend = Path(__file__).resolve().parents[1]
        html = (backend / "frontend" / "index.html").read_text(encoding="utf-8")
        javascript = (backend / "frontend" / "js" / "social-extra.js").read_text(encoding="utf-8")
        main = (backend / "main.py").read_text(encoding="utf-8")
        for element_id in ("facebookConnect", "facebookPublish", "youtubeConnect", "youtubeTitle", "youtubePrivacy", "youtubePublish"):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("platform:'FACEBOOK'", javascript)
        self.assertIn("platform:'YOUTUBE'", javascript)
        self.assertIn('"/api/v1/facebook-auth/callback"', main)
        self.assertIn('"/api/v1/youtube-auth/callback"', main)

    async def test_tiktok_web_is_primary_and_api_is_fallback(self):
        frontend = Path(__file__).resolve().parents[1] / "frontend"
        html = (frontend / "index.html").read_text(encoding="utf-8")
        javascript = (frontend / "js" / "tiktok.js").read_text(encoding="utf-8")

        web_tab = 'class="platform-tab-btn is-active" data-platform-target="tiktok-studio" aria-selected="true"'
        api_tab = 'class="platform-tab-btn" data-platform-target="tiktok-api" aria-selected="false"'
        self.assertIn(web_tab, html)
        self.assertIn(api_tab, html)
        self.assertLess(html.index(web_tab), html.index(api_tab))
        self.assertIn('panel-tiktok-studio is-active', html)
        self.assertIn('panel-tiktok-api is-hidden', html)
        self.assertIn('<span class="tab-btn-title">TikTok Web</span>', html)
        self.assertIn('<span class="tab-btn-badge">Dự phòng</span>', html)
        self.assertIn('aria-label="TikTok Web"', html)
        self.assertIn('aria-label="TikTok API dự phòng"', html)
        self.assertIn("let currentPlatform = 'tiktok-studio';", javascript)
        self.assertIn("vidtrans_social_platform_v2", javascript)
        self.assertIn('id="tiktokRepostButton"', html)
        self.assertIn("prepare({ repost: true })", javascript)
        self.assertIn("chắc chắn muốn tải lại", javascript)
        self.assertIn("resolvedBrowserUrl || lastBrowserUrl", javascript)
        self.assertIn('class="tiktok-repost-button"', html)

    async def test_source_links_include_youtube_and_facebook(self):
        frontend = Path(__file__).resolve().parents[1] / "frontend"
        html = (frontend / "index.html").read_text(encoding="utf-8")
        javascript = (frontend / "js" / "app.js").read_text(encoding="utf-8")

        self.assertIn("TikTok / Douyin / YouTube / Facebook", html)
        for domain in ("youtube.com", "youtu.be", "facebook.com", "fb.watch"):
            self.assertIn(f'"{domain}"', javascript)
        self.assertIn('? "YT"', javascript)
        self.assertIn('? "FB"', javascript)

    async def test_source_browser_workspace_ships_platform_tabs(self):
        frontend = Path(__file__).resolve().parents[1] / "frontend"
        html = (frontend / "index.html").read_text(encoding="utf-8")
        javascript = (frontend / "js" / "app.js").read_text(encoding="utf-8")
        for platform in ("douyin", "youtube", "facebook"):
            self.assertIn(f'data-source-browser-tab="{platform}"', html)
        self.assertIn("/api/v1/source-browser/${platform}/sync", javascript)
        self.assertIn("/api/v1/source-browser/${platform}/activate", javascript)
        self.assertIn("/js/app.js?v=20261015-3", html)
        self.assertIn('button.addEventListener("click", () => selectSourceBrowserTab', javascript)

    async def test_unknown_websocket_is_rejected_without_http_assertion(self):
        with TemporaryDirectory() as directory:
            app = Starlette(routes=[Mount("/", app=FrontendStaticFiles(directory=directory, html=True))])
            for path in ("/", "/websockify", "/douyin-browser/websockifyvnc.html"):
                sent = []
                async def receive():
                    return {"type": "websocket.connect"}
                async def send(message):
                    sent.append(message)
                await app({"type": "websocket", "path": path, "root_path": "", "headers": [],
                           "query_string": b"", "scheme": "ws"}, receive, send)
                self.assertEqual(sent, [{"type": "websocket.close", "code": 1008}])

    async def test_http_frontend_still_serves_index(self):
        with TemporaryDirectory() as directory:
            Path(directory, "index.html").write_text("VidTrans", encoding="utf-8")
            app = FrontendStaticFiles(directory=directory, html=True)
            sent = []
            async def receive():
                return {"type": "http.request"}
            async def send(message):
                sent.append(message)
            await app({"type": "http", "path": "/", "root_path": "", "method": "GET", "headers": []}, receive, send)
            self.assertEqual(sent[0]["status"], 200)
            self.assertEqual(sent[-1]["body"], b"VidTrans")
