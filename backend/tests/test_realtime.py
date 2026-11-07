"""Socket.IO endpoint: authentication and per-account job progress."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import main
from infrastructure.job_events import JobEventHub
from infrastructure.realtime import JOBS_ALL_ROOM, RealtimeServer, user_jobs_room
from tests import test_auth_session_api
from tests.test_auth_session_api import PASSWORD

SOCKET_URL = "/api/v1/socket.io/?EIO=4&transport=websocket"


def socket_io_connect(client: TestClient, auth: dict | None = None, headers: dict | None = None) -> str:
    """Open an Engine.IO v4 WebSocket, send the Socket.IO CONNECT packet, return the reply:
    ``40{"sid": ...}`` when accepted, ``44{...}`` when refused."""
    # Browsers and socket.io clients send these; the test client does not.
    upgrade = {"Upgrade": "websocket", "Connection": "Upgrade"}
    with client.websocket_connect(SOCKET_URL, headers={**upgrade, **(headers or {})}) as socket:
        assert socket.receive_text().startswith("0")  # Engine.IO open
        socket.send_text("40" + json.dumps(auth or {}))
        return socket.receive_text()


class SocketIoAuthTests(unittest.TestCase):
    setUp = test_auth_session_api.AuthSessionApiTests.setUp
    tearDown = test_auth_session_api.AuthSessionApiTests.tearDown

    def access_token(self) -> str:
        response = self.client.post(
            "/api/v1/auth/token",
            data={"username": "editor@test.local", "password": PASSWORD, "client": "native"},
        )
        return response.json()["access_token"]

    def test_app_token_in_auth_payload_connects(self) -> None:
        reply = socket_io_connect(TestClient(main.app), {"token": self.access_token()})

        self.assertTrue(reply.startswith("40"), reply)
        self.assertIn("sid", json.loads(reply[2:]))

    def test_browser_cookie_session_connects_from_its_own_origin(self) -> None:
        self.client.post("/api/v1/auth/token", data={"username": "editor@test.local", "password": PASSWORD})

        same = socket_io_connect(self.client, headers={"Origin": "http://testserver"})
        cross = socket_io_connect(self.client, headers={"Origin": "https://evil.example"})

        self.assertTrue(same.startswith("40"), same)
        self.assertTrue(cross.startswith("44"), cross)

    def test_bad_or_missing_token_is_refused(self) -> None:
        fresh = TestClient(main.app)
        for auth in ({"token": "nope"}, {"token": ""}, {}):
            reply = socket_io_connect(fresh, auth)
            self.assertTrue(reply.startswith("44"), (auth, reply))
            self.assertEqual(json.loads(reply[2:])["message"], "unauthorized")


class RealtimeRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = RealtimeServer(
            JobEventHub(lambda ids: {}),
            authenticate=lambda scope, auth: None,
            scope_message=lambda message, owner, allow_all: main.filter_job_event_for_user(
                message, owner, allow_all=allow_all),
        )
        self.emit = AsyncMock()
        self.server.sio.emit = self.emit

    def emitted(self) -> dict[str | None, dict]:
        return {call.kwargs.get("room"): call.args[1] for call in self.emit.call_args_list}

    def test_each_account_gets_only_its_jobs_and_staff_get_all(self) -> None:
        message = {
            "type": "jobs", "removed": [], "queue_positions": {"a1": 1, "b1": 2},
            "items": [{"job_id": "a1", "_owner_user_id": "user-a"}, {"job_id": "b1", "_owner_user_id": "user-b"}],
        }

        asyncio.run(self.server.publish_jobs(message))

        sent = self.emitted()
        self.assertEqual([item["job_id"] for item in sent[user_jobs_room("user-a")]["items"]], ["a1"])
        self.assertEqual(sent[user_jobs_room("user-b")]["queue_positions"], {"b1": 2})
        self.assertEqual([item["job_id"] for item in sent[JOBS_ALL_ROOM]["items"]], ["a1", "b1"])
        self.assertNotIn("_owner_user_id", sent[JOBS_ALL_ROOM]["items"][0])

    def test_deleted_jobs_make_everyone_reload(self) -> None:
        asyncio.run(self.server.publish_jobs({"type": "jobs", "items": [], "removed": ["x"]}))

        self.emit.assert_awaited_once()
        self.assertIsNone(self.emit.call_args.kwargs.get("room"))
        self.assertTrue(self.emit.call_args.args[1]["resync"])

if __name__ == "__main__":
    unittest.main()
