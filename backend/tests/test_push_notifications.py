"""Mobile devices and job push notifications: one push per job outcome and attempt."""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from application.job_service import JobService
from infrastructure.device_store import MAX_DEVICES_PER_USER, DeviceError, DeviceStore
from infrastructure.job_store import SQLiteJobStore
from infrastructure.push_notifications import FcmError, FcmSender, JobPushNotifier
from tests.test_logo_branding import account


class FakeSender:
    def __init__(self, dead: set[str] | None = None) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.dead = dead or set()

    def send(self, token, *, title, body, data):
        if token in self.dead:
            raise FcmError("gone", error_code="UNREGISTERED")
        self.sent.append((token, data))


class DeviceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DeviceStore(Path(self.temp.name) / "devices.sqlite3")

    def test_token_is_upserted_and_moves_to_the_last_user(self) -> None:
        first = self.store.register("u1", "tok", "android", "1.0.0")
        again = self.store.register("u1", "tok", "android", "1.0.1")
        self.assertEqual(first["device_id"], again["device_id"])
        self.assertEqual(again["app_version"], "1.0.1")
        self.store.register("u2", "tok", "android")
        self.assertEqual(self.store.tokens_for_user("u1"), [])
        self.assertEqual(self.store.tokens_for_user("u2"), ["tok"])

    def test_delete_is_scoped_to_the_owner(self) -> None:
        device = self.store.register("u1", "tok", "ios")
        self.assertFalse(self.store.delete("u2", device["device_id"]))
        self.assertTrue(self.store.delete("u1", device["device_id"]))
        self.assertEqual(self.store.list_for_user("u1"), [])

    def test_rejects_unknown_platform_and_caps_devices(self) -> None:
        with self.assertRaises(DeviceError):
            self.store.register("u1", "tok", "web")
        for index in range(MAX_DEVICES_PER_USER + 3):
            self.store.register("u1", f"tok-{index}", "android")
        self.assertEqual(len(self.store.list_for_user("u1")), MAX_DEVICES_PER_USER)

    def test_delivery_is_claimed_once_until_reset(self) -> None:
        self.assertTrue(self.store.claim_delivery("job", "completed"))
        self.assertFalse(self.store.claim_delivery("job", "completed"))
        self.store.reset_deliveries("job")
        self.assertTrue(self.store.claim_delivery("job", "completed"))


class JobPushNotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.devices = DeviceStore(root / "devices.sqlite3")
        self.sender = FakeSender(dead={"dead-token"})
        self.notifier = JobPushNotifier(self.devices, self.sender, lambda job_id: self.service.get(job_id))
        self.addCleanup(self.notifier.shutdown)
        self.service = JobService(SQLiteJobStore(root / "jobs.sqlite3"))
        self.service.create("job-1", {"status": "queued", "filename": "clip.mp4"}, owner_user_id="u1")
        self.devices.register("u1", "phone", "android")

    def test_pushes_each_outcome_once_per_attempt(self) -> None:
        self.service.update("job-1", status="completed")
        self.assertEqual(self.notifier.deliver("job-1", "completed"), 1)
        # A restarted worker writing the same outcome again stays silent.
        self.assertEqual(self.notifier.deliver("job-1", "completed"), 0)
        self.assertEqual(self.sender.sent, [("phone", {"type": "job_status", "job_id": "job-1", "status": "completed"})])

        self.service.retry("job-1")
        self.devices.reset_deliveries("job-1")
        self.service.update("job-1", status="completed")
        self.assertEqual(self.notifier.deliver("job-1", "completed"), 1)

    def test_skips_stale_status_and_other_users(self) -> None:
        self.devices.register("u2", "other-phone", "ios")
        self.service.update("job-1", status="processing")
        self.assertEqual(self.notifier.deliver("job-1", "completed"), 0)
        self.service.update("job-1", status="failed")
        self.assertEqual(self.notifier.deliver("job-1", "failed"), 1)
        self.assertEqual([token for token, _ in self.sender.sent], ["phone"])

    def test_unregistered_tokens_are_removed(self) -> None:
        self.devices.register("u1", "dead-token", "android")
        self.service.update("job-1", status="completed")
        self.assertEqual(self.notifier.deliver("job-1", "completed"), 1)
        self.assertEqual(self.devices.tokens_for_user("u1"), ["phone"])

    def test_without_credentials_nothing_happens(self) -> None:
        disabled = JobPushNotifier(self.devices, None, lambda job_id: self.service.get(job_id))
        self.assertFalse(disabled.enabled)
        disabled.on_status("job-1", "completed")
        self.assertEqual(disabled.deliver("job-1", "completed"), 0)


class JobServiceStatusHookTests(unittest.TestCase):
    def test_reports_stored_status_writes_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            service = JobService(SQLiteJobStore(Path(temp) / "jobs.sqlite3"),
                                 on_status=lambda job_id, status: calls.append((job_id, status)))
            service.create("j", {"status": "queued"}, owner_user_id="u")
            service.update("j", progress=10)
            service.update("j", status="completed")
            service.retry("j")
            self.assertEqual(calls, [("j", "completed"), ("j", "queued")])


def _rsa_credentials() -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return {"project_id": "vidtrans-test", "client_email": "push@vidtrans-test.iam.gserviceaccount.com",
            "private_key": pem, "token_uri": "https://oauth2.example/token"}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FcmSenderTests(unittest.TestCase):
    def test_caches_access_token_and_maps_unregistered(self) -> None:
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            if request.full_url.endswith("/token"):
                return _Response(json.dumps({"access_token": "oauth", "expires_in": 3600}).encode())
            if json.loads(request.data)["message"]["token"] == "gone":
                body = json.dumps({"error": {"status": "NOT_FOUND", "details": [
                    {"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": "UNREGISTERED"}]}})
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(body.encode()))
            return _Response(b"{}")

        sender = FcmSender(_rsa_credentials(), urlopen=urlopen)
        sender.send("ok", title="t", body="b", data={"job_id": "j"})
        with self.assertRaises(FcmError) as caught:
            sender.send("gone", title="t", body="b", data={"job_id": "j"})
        self.assertEqual(caught.exception.error_code, "UNREGISTERED")
        token_calls = [r for r in requests if r.full_url.endswith("/token")]
        self.assertEqual(len(token_calls), 1)
        sends = [r for r in requests if "messages:send" in r.full_url]
        self.assertEqual(sends[0].get_header("Authorization"), "Bearer oauth")
        self.assertIn("/projects/vidtrans-test/", sends[0].full_url)

    def test_missing_credentials_file_disables_push(self) -> None:
        with patch.dict("os.environ", {"VIDTRANS_FCM_CREDENTIALS_FILE": "/nonexistent.json"}):
            self.assertIsNone(FcmSender.from_env())
        with patch.dict("os.environ", {"VIDTRANS_FCM_CREDENTIALS_FILE": ""}):
            self.assertIsNone(FcmSender.from_env())


class DeviceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DeviceStore(Path(self.temp.name) / "devices.sqlite3")
        self.users = {"a": account("user-a"), "b": account("user-b")}
        for active in (
            patch.object(main.DEVICE_ROUTE_DEPS, "store", self.store),
            patch.object(main, "current_account", lambda request: self.users[request.headers.get("x-test-user", "a")]),
        ):
            active.start()
            self.addCleanup(active.stop)
        self.client = TestClient(main.app)

    def test_register_list_and_delete_per_account(self) -> None:
        response = self.client.post("/api/v1/me/devices", json={"token": "fcm-1", "platform": "ios", "app_version": "1.0.0"})
        self.assertEqual(response.status_code, 200)
        device = response.json()
        self.assertIn("push_enabled", device)
        self.assertNotIn("token", device)

        self.assertEqual(len(self.client.get("/api/v1/me/devices").json()["items"]), 1)
        self.assertEqual(self.client.get("/api/v1/me/devices", headers={"x-test-user": "b"}).json()["items"], [])
        other = self.client.delete(f"/api/v1/me/devices/{device['device_id']}", headers={"x-test-user": "b"})
        self.assertEqual(other.status_code, 404)
        self.assertEqual(self.client.delete(f"/api/v1/me/devices/{device['device_id']}").status_code, 204)

    def test_validates_platform(self) -> None:
        response = self.client.post("/api/v1/me/devices", json={"token": "fcm-1", "platform": "web"})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
