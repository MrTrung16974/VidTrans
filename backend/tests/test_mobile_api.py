"""API behaviour native (Flutter) clients rely on: body tokens, bearer WebSocket, OAuth deep links."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

import main
from infrastructure.admin_accounts import AdminAccountStore
from infrastructure.social_credentials import SocialCredentialStore
from tests.test_auth_session_api import PASSWORD, AuthSessionApiTests

APP_REDIRECTS = {"VIDTRANS_APP_REDIRECT_URIS": "vidtrans://oauth, https://vidtrans.cloud/app/oauth"}
CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"


class NativeAuthTests(unittest.TestCase):
    setUp = AuthSessionApiTests.setUp
    tearDown = AuthSessionApiTests.tearDown

    def native_login(self, client: TestClient):
        return client.post(
            "/api/v1/auth/token",
            data={"username": "editor@test.local", "password": PASSWORD, "client": "native"},
        )

    def test_native_login_returns_tokens_in_body_without_cookies(self) -> None:
        response = self.native_login(self.client)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["access_token"] and body["refresh_token"])
        self.assertEqual(body["refresh_expires_in"], 5 * 86400)
        self.assertEqual(response.headers.get_list("set-cookie"), [])
        me = TestClient(main.app).get("/api/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        self.assertEqual(me.status_code, 200)

    def test_web_login_keeps_refresh_token_out_of_the_body(self) -> None:
        response = self.client.post("/api/v1/auth/token", data={"username": "editor@test.local", "password": PASSWORD})
        self.assertNotIn("refresh_token", response.json())
        self.assertTrue(response.headers.get_list("set-cookie"))

    def test_unknown_client_type_is_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/auth/token",
            data={"username": "editor@test.local", "password": PASSWORD, "client": "desktop"},
        )
        self.assertEqual(response.status_code, 422)

    def test_body_refresh_needs_no_cookie_or_csrf_header(self) -> None:
        refresh_token = self.native_login(self.client).json()["refresh_token"]
        fresh = TestClient(main.app)

        refreshed = fresh.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})

        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        token = refreshed.json()["access_token"]
        self.assertEqual(refreshed.headers.get_list("set-cookie"), [])
        self.assertEqual(fresh.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code, 200)

    def test_body_refresh_rejects_access_tokens_and_bad_bodies(self) -> None:
        access_token = self.native_login(self.client).json()["access_token"]
        fresh = TestClient(main.app)
        self.assertEqual(fresh.post("/api/v1/auth/refresh", json={"refresh_token": access_token}).status_code, 401)
        self.assertEqual(fresh.post("/api/v1/auth/refresh", json={"token": "x"}).status_code, 422)
        # The cookie flow is unchanged: without a body it still needs the anti-CSRF header.
        self.assertEqual(fresh.post("/api/v1/auth/refresh").status_code, 403)

    def test_websocket_accepts_bearer_token_from_any_origin(self) -> None:
        token = self.native_login(self.client).json()["access_token"]
        fresh = TestClient(main.app)
        headers = {"Authorization": f"Bearer {token}", "Origin": "https://elsewhere.example"}
        with fresh.websocket_connect("/api/v1/jobs/events", headers=headers) as socket:
            socket.close()

    def test_websocket_rejects_bad_bearer_and_cross_origin_cookies(self) -> None:
        fresh = TestClient(main.app)
        with self.assertRaises(WebSocketDisconnect):
            with fresh.websocket_connect("/api/v1/jobs/events", headers={"Authorization": "Bearer nope"}) as socket:
                socket.receive_text()
        with self.assertRaises(WebSocketDisconnect):
            with fresh.websocket_connect("/api/v1/jobs/events", headers={"Authorization": "Basic abc"}) as socket:
                socket.receive_text()
        self.client.post("/api/v1/auth/token", data={"username": "editor@test.local", "password": PASSWORD})
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(
                "/api/v1/jobs/events", headers={"Origin": "https://evil.example"}
            ) as socket:
                socket.receive_text()


class OAuthDeepLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        database = Path(self.temp.name) / "social.sqlite3"
        user = AdminAccountStore(database, "owner").create(email="user-a@test.local", display_name="A")
        self.user_id = str(user["user_id"])
        self.store = SocialCredentialStore(database)
        for active in (patch.object(main, "SOCIAL_CREDENTIALS", self.store), patch.dict(os.environ, APP_REDIRECTS)):
            active.start()
            self.addCleanup(active.stop)

    def register(self, return_to: str, state: str = "state-1") -> None:
        query = f"return_to={return_to}".encode() if return_to else b""
        request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": query})
        request.state.account = {"user_id": self.user_id, "role": "editor"}
        main.register_tenant_oauth_session(request, "threads", f"https://threads.example/oauth?state={state}")

    def test_allowlist_matches_the_link_without_its_query(self) -> None:
        self.assertTrue(main.app_redirect_allowed("vidtrans://oauth"))
        self.assertTrue(main.app_redirect_allowed("vidtrans://oauth?from=settings"))
        self.assertTrue(main.app_redirect_allowed("https://vidtrans.cloud/app/oauth"))
        self.assertFalse(main.app_redirect_allowed("vidtrans://other"))
        self.assertFalse(main.app_redirect_allowed("https://evil.example/app/oauth"))
        with patch.dict(os.environ, {"VIDTRANS_APP_REDIRECT_URIS": ""}):
            self.assertFalse(main.app_redirect_allowed("vidtrans://oauth"))

    def test_unlisted_return_to_is_refused(self) -> None:
        with self.assertRaises(HTTPException) as refused:
            self.register("vidtrans://evil")
        self.assertEqual(refused.exception.status_code, 400)

    def test_callback_returns_to_the_app_with_the_result(self) -> None:
        self.register("vidtrans://oauth")
        response = TestClient(main.app).get(
            "/api/v1/threads-auth/callback",
            params={"state": "state-1", "error": "access_denied"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "vidtrans://oauth?threads_error=access_denied")

    def test_web_sessions_keep_the_web_redirect(self) -> None:
        self.register("")
        response = TestClient(main.app).get(
            "/api/v1/threads-auth/callback",
            params={"state": "state-1", "error": "access_denied"},
            follow_redirects=False,
        )
        self.assertEqual(response.headers["location"], "/?threads_error=access_denied#tiktok")


class ContractTests(unittest.TestCase):
    def test_contract_lists_every_api_operation(self) -> None:
        """contracts/openapi.json is what Flutter generates its client from; keep it in sync.

        Regenerate with: cd backend && python scripts/export_openapi.py
        """
        live = main.app.openapi()["paths"]
        stored = json.loads(CONTRACT.read_text(encoding="utf-8"))["paths"]
        operations = lambda paths: {(path, method) for path, item in paths.items() for method in item}  # noqa: E731
        self.assertEqual(operations(stored), operations(live))


if __name__ == "__main__":
    unittest.main()
