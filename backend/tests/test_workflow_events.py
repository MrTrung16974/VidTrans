import asyncio
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.workflow_events import (
    N8nWebhookClient,
    N8nWebhookConfig,
    N8nWorkflowDispatcher,
    WorkflowEventSink,
    WorkflowEventStore,
)


class _Response:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class WorkflowEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = WorkflowEventStore(Path(self.temp_dir.name) / "jobs.sqlite3")
        self.sink = WorkflowEventSink(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_event_payload_is_allow_listed(self) -> None:
        event = self.sink.emit(
            "source.uploaded",
            "job-1",
            {
                "status": "queued",
                "step": "queued",
                "source_language": "zh-CN",
                "target_language": "vi",
                "filename": "private-name.mp4",
                "source_url": "https://example.invalid/private",
                "video_path": "/secret/path/video.mp4",
                "resume_request": {"source_cookie_path": "/secret/cookies.txt"},
            },
        )

        encoded = json.dumps(event)
        self.assertEqual(event["job"]["job_id"], "job-1")
        self.assertEqual(event["job"]["source_language"], "zh-CN")
        self.assertNotIn("private-name", encoded)
        self.assertNotIn("example.invalid", encoded)
        self.assertNotIn("/secret/", encoded)

    def test_transition_emits_only_business_milestones(self) -> None:
        no_events = self.sink.emit_transition(
            "job-1",
            {"status": "processing", "step": "translating", "progress": 0.41},
            {"status": "processing", "step": "translating", "progress": 0.42},
        )
        events = self.sink.emit_transition(
            "job-1",
            {"status": "processing", "step": "translating"},
            {"status": "processing", "step": "translation-ready", "progress": 0.46},
        )

        self.assertEqual(no_events, [])
        self.assertEqual([event["event_type"] for event in events], ["translation.completed"])

    def test_source_ready_distinguishes_upload_from_social_download(self) -> None:
        upload_events = self.sink.emit_transition(
            "job-upload",
            {"step": "queued"},
            {"step": "source-ready", "status": "processing"},
        )
        download_events = self.sink.emit_transition(
            "job-download",
            {"step": "downloading-source"},
            {"step": "source-ready", "status": "processing", "source_platform": "douyin"},
        )

        self.assertEqual(upload_events[0]["event_type"], "source.ready")
        self.assertEqual(download_events[0]["event_type"], "download.completed")

    def test_store_claim_delivery_and_recovery(self) -> None:
        event = self.sink.emit("job.completed", "job-1", {"status": "completed"})
        claimed = self.store.claim_next()

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["event_id"], event["event_id"])
        self.assertEqual(claimed["attempts"], 1)
        self.store.mark_delivered(event["event_id"])
        self.assertEqual(self.store.get(event["event_id"])["status"], "delivered")

    def test_failed_event_retries_then_becomes_dead(self) -> None:
        event = self.sink.emit("job.failed", "job-1", {"status": "failed"})
        first = self.store.claim_next()
        self.store.mark_failed(
            event["event_id"], attempts=first["attempts"], max_attempts=2, error="offline"
        )
        self.assertEqual(self.store.get(event["event_id"])["status"], "pending")

        self.store.mark_failed(event["event_id"], attempts=2, max_attempts=2, error="offline")
        self.assertEqual(self.store.get(event["event_id"])["status"], "dead")

        items, total = self.store.list_events(status="dead")
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["event_id"], event["event_id"])
        self.assertNotIn("payload_json", items[0])
        self.assertEqual(self.store.summary()["dead"], 1)

        self.assertTrue(self.store.replay_dead(event["event_id"]))
        replayed = self.store.get(event["event_id"])
        self.assertEqual(replayed["status"], "pending")
        self.assertEqual(replayed["attempts"], 0)
        self.assertIsNone(replayed["last_error"])

    def test_only_dead_events_can_be_replayed(self) -> None:
        event = self.sink.emit("job.completed", "job-1", {"status": "completed"})

        self.assertFalse(self.store.replay_dead(event["event_id"]))

    def test_list_rejects_unknown_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid"):
            self.store.list_events(status="unknown")

    def test_webhook_client_signs_exact_request_body(self) -> None:
        event = self.sink.emit("job.completed", "job-1", {"status": "completed"})
        config = N8nWebhookConfig(
            webhook_url="https://n8n.example.test/webhook/vidtrans",
            secret="test-secret",
        )
        client = N8nWebhookClient(config)

        with patch("infrastructure.workflow_events.urllib.request.urlopen", return_value=_Response()) as call:
            client.send(event)

        request = call.call_args.args[0]
        expected = hmac.new(b"test-secret", request.data, hashlib.sha256).hexdigest()
        self.assertEqual(request.headers["X-vidtrans-signature"], f"sha256={expected}")
        self.assertEqual(request.headers["X-vidtrans-event-id"], event["event_id"])
        self.assertEqual(request.headers["Authorization"], "Bearer test-secret")

    def test_remote_http_webhook_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            N8nWebhookConfig(
                webhook_url="http://n8n.example.test/webhook/vidtrans",
                secret="secret",
            ).validate()

    def test_private_dev_http_requires_explicit_override(self) -> None:
        N8nWebhookConfig(
            webhook_url="http://n8n:5678/webhook/vidtrans",
            secret="secret",
            allow_insecure_http=True,
        ).validate()


class _RecordingClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.config = N8nWebhookConfig(
            webhook_url="http://localhost:5678/webhook/vidtrans",
            secret="test-secret",
            max_attempts=3,
        )
        self.fail = fail
        self.events = []

    def send(self, event):
        self.events.append(event)
        if self.fail:
            raise RuntimeError("n8n unavailable")


class WorkflowDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = WorkflowEventStore(Path(self.temp_dir.name) / "jobs.sqlite3")
        self.sink = WorkflowEventSink(self.store)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_dispatcher_delivers_pending_event(self) -> None:
        event = self.sink.emit("job.completed", "job-1", {"status": "completed"})
        client = _RecordingClient()
        dispatcher = N8nWorkflowDispatcher(self.store, client, poll_interval=0.01)

        await dispatcher.start()
        for _ in range(50):
            if self.store.get(event["event_id"])["status"] == "delivered":
                break
            await asyncio.sleep(0.01)
        await dispatcher.stop()

        self.assertEqual(self.store.get(event["event_id"])["status"], "delivered")
        self.assertEqual(client.events[0]["event_id"], event["event_id"])

    async def test_dispatcher_outage_keeps_event_for_retry(self) -> None:
        event = self.sink.emit("job.failed", "job-1", {"status": "failed"})
        client = _RecordingClient(fail=True)
        dispatcher = N8nWorkflowDispatcher(self.store, client, poll_interval=0.01)

        await dispatcher.start()
        for _ in range(50):
            current = self.store.get(event["event_id"])
            if current["attempts"] == 1 and current["status"] == "pending":
                break
            await asyncio.sleep(0.01)
        await dispatcher.stop()

        current = self.store.get(event["event_id"])
        self.assertEqual(current["status"], "pending")
        self.assertEqual(current["attempts"], 1)
        self.assertIn("unavailable", current["last_error"])


if __name__ == "__main__":
    unittest.main()
