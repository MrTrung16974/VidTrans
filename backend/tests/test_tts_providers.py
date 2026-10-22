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
from infrastructure.tts_providers import (
    elevenlabs_voice_preview,
    elevenlabs_voices,
    ensure_provider_configured,
    normalize_tts_provider,
    provider_statuses,
    resolve_elevenlabs_voice_selection,
    synthesize_elevenlabs,
)

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
        self.assertEqual(statuses, {"edge": True, "azure": False, "elevenlabs": False, "cit": False})
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
    def test_library_voice_on_free_plan_explains_the_limit(self, mocked_open) -> None:
        mocked_open.side_effect = http_error(
            402, b'{"detail":{"type":"payment_required","code":"paid_plan_required"}}'
        )

        with self.assertRaisesRegex(RuntimeError, "gói Free không được dùng giọng trong Voice Library"):
            self.synthesize()

        self.assertEqual(mocked_open.call_count, 1)
        self.sleep.assert_not_called()

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

MULTI_VOICE_ENV = {
    "VIDTRANS_ELEVENLABS_API_KEY": "secret",
    "VIDTRANS_ELEVENLABS_VOICES": "Nữ kể chuyện|female|f-1\nNữ trẻ|female|f-2; Nam trầm|male|m-1\nsai định dạng\nLạ|robot|x-1",
}


class ElevenLabsVoiceListTest(unittest.TestCase):
    def setUp(self) -> None:
        tts_providers._elevenlabs_ready_voices.clear()
        tts_providers._elevenlabs_voice_locks.clear()

    @patch.dict(os.environ, MULTI_VOICE_ENV, clear=True)
    def test_parses_voice_list_and_skips_malformed_entries(self) -> None:
        voices = elevenlabs_voices()
        self.assertEqual([(v.label, v.gender, v.voice_id) for v in voices], [
            ("Nữ kể chuyện", "female", "f-1"),
            ("Nữ trẻ", "female", "f-2"),
            ("Nam trầm", "male", "m-1"),
        ])
        self.assertEqual(len({voice.key for voice in voices}), 3)

    @patch.dict(os.environ, {**MULTI_VOICE_ENV, "VIDTRANS_ELEVENLABS_MALE_VOICE_ID": "m-legacy"}, clear=True)
    def test_status_lists_voices_without_ids_and_merges_legacy_pair(self) -> None:
        status = next(item for item in provider_statuses() if item.key == "elevenlabs")
        self.assertTrue(status.configured)
        self.assertEqual([voice["label"] for voice in status.voices], ["Nữ kể chuyện", "Nữ trẻ", "Nam trầm", "Nam — ElevenLabs"])
        self.assertNotIn("f-1", json.dumps(status.__dict__, ensure_ascii=False))
        self.assertEqual(status.female_voice_label, "Nữ kể chuyện")

    @patch.dict(os.environ, {"VIDTRANS_ELEVENLABS_API_KEY": "secret"}, clear=True)
    def test_requires_at_least_one_voice(self) -> None:
        with self.assertRaisesRegex(ValueError, "VIDTRANS_ELEVENLABS_VOICES"):
            ensure_provider_configured("elevenlabs")

    @patch.dict(os.environ, MULTI_VOICE_ENV, clear=True)
    def test_selection_defaults_and_rejects_unknown_key(self) -> None:
        keys = {voice.label: voice.key for voice in elevenlabs_voices()}
        self.assertEqual(
            resolve_elevenlabs_voice_selection(keys["Nữ trẻ"], ""),
            {"female": keys["Nữ trẻ"], "male": keys["Nam trầm"]},
        )
        with self.assertRaisesRegex(ValueError, "không còn"):
            resolve_elevenlabs_voice_selection("v-unknown", "")

    @patch.dict(os.environ, {"VIDTRANS_ELEVENLABS_API_KEY": "secret", "VIDTRANS_ELEVENLABS_VOICES": "Chỉ nữ|female|f-1"}, clear=True)
    def test_gender_without_voice_falls_back_to_any_voice(self) -> None:
        selection = resolve_elevenlabs_voice_selection()
        self.assertEqual(selection["male"], selection["female"])

    @patch.dict(os.environ, MULTI_VOICE_ENV, clear=True)
    @patch("infrastructure.tts_providers.urlopen")
    def test_selected_voice_key_picks_its_voice_id(self, mocked_open) -> None:
        mocked_open.return_value = audio_response()
        keys = {voice.label: voice.key for voice in elevenlabs_voices()}
        with tempfile.TemporaryDirectory() as temp_dir:
            synthesize_elevenlabs("Xin chào", Path(temp_dir) / "a.mp3", voice_type="female", voice_key=keys["Nữ trẻ"])
            synthesize_elevenlabs("Xin chào", Path(temp_dir) / "b.mp3", voice_type="male")
        urls = [call.args[0].full_url for call in mocked_open.call_args_list]
        self.assertIn("/text-to-speech/f-2?", urls[0])
        self.assertIn("/text-to-speech/m-1?", urls[1])


def json_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
    return response


def clip_response(body: bytes = b"ID3clip", content_type: str = "audio/mpeg") -> MagicMock:
    response = MagicMock()
    inner = response.__enter__.return_value
    inner.read.return_value = body
    inner.headers.get_content_type.return_value = content_type
    return response


class ElevenLabsPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        tts_providers._elevenlabs_previews.clear()
        patch.dict(os.environ, MULTI_VOICE_ENV, clear=True).start()
        self.addCleanup(patch.stopall)
        self.key = {voice.label: voice.key for voice in elevenlabs_voices()}["Nữ trẻ"]

    @patch("infrastructure.tts_providers.urlopen")
    def test_returns_and_caches_account_voice_preview(self, mocked_open) -> None:
        mocked_open.side_effect = [
            json_response({"preview_url": "https://storage.googleapis.com/eleven/f-2.mp3"}),
            clip_response(),
        ]

        first = elevenlabs_voice_preview(self.key)
        second = elevenlabs_voice_preview(self.key)

        self.assertEqual(first, (b"ID3clip", "audio/mpeg"))
        self.assertEqual(second, first)
        self.assertEqual(mocked_open.call_count, 2)
        self.assertIn("/v1/voices/f-2", mocked_open.call_args_list[0].args[0].full_url)

    @patch("infrastructure.tts_providers.urlopen")
    def test_library_voice_falls_back_to_shared_voices(self, mocked_open) -> None:
        mocked_open.side_effect = [
            http_error(404),
            json_response({"voices": [
                {"voice_id": "other", "preview_url": "https://storage.googleapis.com/x.mp3"},
                {"voice_id": "f-2", "preview_url": "https://storage.googleapis.com/f-2.mp3"},
            ]}),
            clip_response(),
        ]

        audio, _ = elevenlabs_voice_preview(self.key)

        self.assertEqual(audio, b"ID3clip")
        self.assertEqual(mocked_open.call_args_list[2].args[0].full_url, "https://storage.googleapis.com/f-2.mp3")

    @patch("infrastructure.tts_providers.urlopen")
    def test_rejects_preview_on_unexpected_host(self, mocked_open) -> None:
        mocked_open.side_effect = [json_response({"preview_url": "http://169.254.169.254/latest"})]

        with self.assertRaisesRegex(RuntimeError, "không hợp lệ"):
            elevenlabs_voice_preview(self.key)

        self.assertEqual(mocked_open.call_count, 1)

    def test_unknown_voice_key_is_not_found(self) -> None:
        with self.assertRaises(LookupError):
            elevenlabs_voice_preview("v-unknown")


if __name__ == "__main__":
    unittest.main()


class TTSAccountBlockedTests(unittest.TestCase):
    def test_quota_auth_and_rate_limit_errors_switch_provider(self) -> None:
        from infrastructure.tts_providers import TTSProviderHTTPError, tts_account_blocked

        quota = TTSProviderHTTPError("ElevenLabs", 401, '{"detail":{"status":"quota_exceeded","message":"14 credits remaining"}}')
        self.assertEqual(tts_account_blocked(quota), "hết credit/hạn mức")
        wrapped = RuntimeError("worker failed")
        wrapped.__cause__ = TTSProviderHTTPError("ElevenLabs", 429, "too many")
        self.assertEqual(tts_account_blocked(wrapped), "bị giới hạn tần suất")
        self.assertIn("401", tts_account_blocked(TTSProviderHTTPError("Azure Speech", 401, "invalid key")))
        self.assertIsNone(tts_account_blocked(TTSProviderHTTPError("ElevenLabs", 500, "oops")))
        self.assertIsNone(tts_account_blocked(ValueError("bad text")))
