import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from starlette.applications import Starlette
from starlette.routing import Mount

from infrastructure.frontend_static import FrontendStaticFiles


class FrontendStaticTests(unittest.IsolatedAsyncioTestCase):
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
