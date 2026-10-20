from __future__ import annotations

import os
import json
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from infrastructure import tts_providers
from infrastructure.tts_providers import ensure_provider_configured, normalize_tts_provider, provider_statuses, synthesize_elevenlabs

ELEVENLABS_ENV = {
    "VIDTRANS_ELEVENLABS_API_KEY": "secret",
    "VIDTRANS_ELEVENLABS_FEMALE_VOICE_ID": "female-id",
    "VIDTRANS_ELEVENLABS_MALE_VOICE_ID": "male-id",
}
ALREADY_RUNNING = b'{"detail":{"type":"conflict","code":"already_running","status":"already_running"}}'


def http_error(status: int, body: bytes = b"{}") -> HTTPError:
    return HTTPError("https://api.elevenlabs.io", status, "error", {}, BytesIO(body))


def audio_response() -> MagicMock:
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"audio"
    return response


class TTSProvidersTest(unittest.TestCase):
    def test_edge_is_default_and_always_configured(self) -> None:
        self.assertEqual(normalize_tts_provider(None), "edge")
        ensure_provider_configured("edge")

    def test_unknown_provider_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "tts_provider"):
            normalize_tts_provider("unknown")

    @patch.dict(os.environ, {}, clear=True)
    def test_cloud_providers_require_server_credentials(self) -> None:
        statuses = {item.key: item.configured for item in provider_statuses()}
        self.assertEqual(statuses, {"edge": True, "azure": False, "elevenlabs": False})
        with self.assertRaisesRegex(ValueError, "Azure Speech"):
            ensure_provider_configured("azure")
        with self.assertRaisesRegex(ValueError, "ElevenLabs"):
            ensure_provider_configured("elevenlabs")

    @patch.dict(os.environ, {
        "VIDTRANS_AZURE_SPEECH_KEY": "secret",
        "VIDTRANS_AZURE_SPEECH_REGION": "southeastasia",
        "VIDTRANS_ELEVENLABS_API_KEY": "secret",
        "VIDTRANS_ELEVENLABS_FEMALE_VOICE_ID": "female-id",
        "VIDTRANS_ELEVENLABS_MALE_VOICE_ID": "male-id",
    }, clear=True)
    def test_cloud_providers_report_configured(self) -> None:
        statuses = {item.key: item.configured for item in provider_statuses()}
        self.assertTrue(statuses["azure"])
        self.assertTrue(statuses["elevenlabs"])

    @patch.dict(os.environ, {
        "VIDTRANS_ELEVENLABS_API_KEY": "secret",
        "VIDTRANS_ELEVENLABS_FEMALE_VOICE_ID": "female-id",
        "VIDTRANS_ELEVENLABS_MALE_VOICE_ID": "male-id",
        "VIDTRANS_ELEVENLABS_FEMALE_VOICE_LABEL": "Nữ kể chuyện",
        "VIDTRANS_ELEVENLABS_MALE_VOICE_LABEL": "Nam bản tin",
    }, clear=True)
    def test_elevenlabs_exposes_labels_without_voice_ids(self) -> None:
        status = next(item for item in provider_statuses() if item.key == "elevenlabs")
        self.assertEqual(status.female_voice_label, "Nữ kể chuyện")
        self.assertEqual(status.male_voice_label, "Nam bản tin")
        self.assertNotIn("female-id", status.__dict__.values())

    @patch.dict(os.environ, {
        "VIDTRANS_ELEVENLABS_API_KEY": "secret",
        "VIDTRANS_ELEVENLABS_FEMALE_VOICE_ID": "female-id",
        "VIDTRANS_ELEVENLABS_MALE_VOICE_ID": "male-id",
    }, clear=True)
    @patch("infrastructure.tts_providers.urlopen")
    def test_elevenlabs_receives_selected_style_and_speed(self, mocked_open) -> None:
        response = mocked_open.return_value.__enter__.return_value
        response.read.return_value = b"audio"
        with tempfile.TemporaryDirectory() as temp_dir:
            synthesize_elevenlabs(
                "Xin chào",
                Path(temp_dir) / "voice.mp3",
                voice_type="female",
                voice_style="energetic",
                speed=1.15,
            )
        request = mocked_open.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["voice_settings"]["speed"], 1.15)
        self.assertLess(payload["voice_settings"]["stability"], 0.5)
        self.assertGreater(payload["voice_settings"]["style"], 0.0)



class ElevenLabsConcurrencyTest(unittest.TestCase):
    def setUp(self) -> None:
        tts_providers._elevenlabs_ready_voices.clear()
        tts_providers._elevenlabs_voice_locks.clear()
        self.sleep = patch("infrastructure.tts_providers.time.sleep").start()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, ELEVENLABS_ENV, clear=True).start()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def synthesize(self, name: str = "voice.mp3") -> None:
        synthesize_elevenlabs("Xin chào", Path(self.temp.name) / name, voice_type="female")

    @patch("infrastructure.tts_providers.urlopen")
    def test_already_running_conflict_is_retried(self, mocked_open) -> None:
        mocked_open.side_effect = [http_error(409, ALREADY_RUNNING), audio_response()]

        self.synthesize()

        self.assertEqual(mocked_open.call_count, 2)
        self.sleep.assert_called_once()

    @patch("infrastructure.tts_providers.urlopen")
    def test_non_retryable_error_fails_immediately(self, mocked_open) -> None:
        mocked_open.side_effect = http_error(401, b"unauthorized")

        with self.assertRaisesRegex(RuntimeError, "ElevenLabs API lỗi HTTP 401"):
            self.synthesize()

        self.assertEqual(mocked_open.call_count, 1)
        self.sleep.assert_not_called()

    @patch("infrastructure.tts_providers.urlopen")
    def test_persistent_conflict_gives_up_after_attempts(self, mocked_open) -> None:
        mocked_open.side_effect = lambda *_args, **_kwargs: (_ for _ in ()).throw(http_error(409, ALREADY_RUNNING))

        with self.assertRaisesRegex(RuntimeError, "HTTP 409"):
            self.synthesize()

        self.assertEqual(mocked_open.call_count, tts_providers.ELEVENLABS_ATTEMPTS)

    @patch("infrastructure.tts_providers.urlopen")
    def test_first_request_per_voice_runs_alone_then_parallel(self, mocked_open) -> None:
        events = []
        events_lock = threading.Lock()
        all_waiting = threading.Barrier(5)
        later_overlap = threading.Barrier(4, timeout=2)

        def fake_open(*_args, **_kwargs):
            with events_lock:
                events.append("start")
                is_first = len(events) == 1
            if is_first:
                threading.Event().wait(0.1)
            else:
                later_overlap.wait()  # only passes if 4 requests run concurrently
            with events_lock:
                events.append("end")
            return audio_response()

        def run(index: int) -> None:
            all_waiting.wait()
            self.synthesize(f"v{index}.mp3")

        mocked_open.side_effect = fake_open
        threads = [threading.Thread(target=run, args=(i,)) for i in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(events[:2], ["start", "end"])
        self.assertEqual(mocked_open.call_count, 5)
        self.assertFalse(later_overlap.broken)

if __name__ == "__main__":
    unittest.main()
