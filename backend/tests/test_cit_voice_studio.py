from __future__ import annotations

import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from infrastructure import tts_providers
from infrastructure.tts_providers import (
    TTSProviderHTTPError,
    TTSProviderUnavailable,
    cit_voices,
    ensure_provider_configured,
    provider_statuses,
    resolve_tts_voice_selection,
    synthesize_cit,
    tts_account_blocked,
)

CIT_ENV = {"VIDTRANS_CIT_VOICE_URL": "http://cit.local:8001/"}
VOICES = [
    {"id": "Minh Đức", "gender": "male", "description": "Bắc, tin tức"},
    {"id": "Ngọc Huyền", "gender": "female"},
    "Giọng nhân bản",
]


def response(body: bytes, content_type: str = "application/json") -> MagicMock:
    handle = MagicMock()
    inner = handle.__enter__.return_value
    inner.status = 200
    inner.read.return_value = body
    inner.headers.get_content_type.return_value = content_type
    return handle


def fake_cit(audio: bytes = b"RIFF0000WAVE"):
    calls = []

    def open_(request, timeout=None):
        calls.append(request)
        path = request.full_url.removeprefix("http://cit.local:8001")
        if path == "/health":
            return response(b'{"status":"ok"}')
        if path == "/voices":
            return response(json.dumps({"voices": VOICES}).encode())
        if path == "/api/tts/generate":
            return response(audio, "audio/wav")
        raise AssertionError(path)

    return open_, calls


class CITVoiceStudioProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        tts_providers._cit_cache.clear()
        tts_providers._cit_previews.clear()
        self.addCleanup(tts_providers._cit_cache.clear)
        self.addCleanup(tts_providers._cit_previews.clear)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    @patch.dict(os.environ, {}, clear=True)
    def test_disabled_without_url(self) -> None:
        status = next(item for item in provider_statuses() if item.key == "cit")
        self.assertFalse(status.configured)
        with self.assertRaisesRegex(ValueError, "VIDTRANS_CIT_VOICE_URL"):
            ensure_provider_configured("cit")

    @patch.dict(os.environ, CIT_ENV, clear=True)
    def test_closed_app_reports_not_configured(self) -> None:
        with patch("infrastructure.tts_providers.urlopen", side_effect=URLError("refused")):
            status = next(item for item in provider_statuses() if item.key == "cit")
            self.assertFalse(status.configured)
            with self.assertRaisesRegex(ValueError, "mở ứng dụng"):
                ensure_provider_configured("cit")

    @patch.dict(os.environ, CIT_ENV, clear=True)
    def test_voices_are_parsed_with_gender(self) -> None:
        opener, _ = fake_cit()
        with patch("infrastructure.tts_providers.urlopen", side_effect=opener):
            voices = {voice.key: voice for voice in cit_voices()}
            status = next(item for item in provider_statuses() if item.key == "cit")
        self.assertEqual(voices["Minh Đức"].gender, "male")
        self.assertIn("Bắc, tin tức", voices["Minh Đức"].label)
        self.assertEqual(voices["Ngọc Huyền"].gender, "female")
        self.assertIn("Giọng nhân bản", voices)
        self.assertTrue(status.configured)
        self.assertFalse(status.supports_pitch_matching)
        self.assertEqual(status.male_voice_label, voices["Minh Đức"].label)

    @patch.dict(os.environ, CIT_ENV, clear=True)
    def test_busy_app_keeps_the_last_voice_list(self) -> None:
        opener, calls = fake_cit()
        clock = [1000.0]
        with patch("infrastructure.tts_providers.time.monotonic", lambda: clock[0]):
            with patch("infrastructure.tts_providers.urlopen", side_effect=opener):
                self.assertEqual(len(cit_voices()), 3)
            # /health is slow while the app synthesises; it must not decide availability.
            self.assertNotIn("/health", " ".join(request.full_url for request in calls))
            clock[0] += tts_providers.CIT_STATUS_TTL_SECONDS + 1
            # A timeout means the app is synthesising: keep the list however long that takes.
            with patch("infrastructure.tts_providers.urlopen", side_effect=URLError(TimeoutError("busy"))):
                clock[0] += tts_providers.CIT_STALE_SECONDS * 5
                self.assertEqual(len(cit_voices()), 3)
                ensure_provider_configured("cit")
            # A refused connection means it was closed: keep the list only briefly.
            with patch("infrastructure.tts_providers.urlopen", side_effect=URLError(ConnectionRefusedError())) as refused:
                clock[0] += 1
                self.assertEqual(len(cit_voices()), 3)
                refused.assert_not_called()  # "Busy" is remembered for the TTL, so no new wait.
                clock[0] += tts_providers.CIT_STALE_SECONDS
                self.assertEqual(cit_voices(), [])

    @patch.dict(os.environ, {**CIT_ENV, "VIDTRANS_CIT_VOICE_API_KEY": "lan-key"}, clear=True)
    def test_generate_sends_voice_speed_language_and_key(self) -> None:
        opener, calls = fake_cit()
        output = Path(self.temp.name) / "out.mp3"
        with patch("infrastructure.tts_providers.urlopen", side_effect=opener):
            synthesize_cit("Xin chào", output, voice_key="Minh Đức", speed=2.0, language="zh-CN")
        request = calls[-1]
        payload = json.loads(request.data)
        self.assertEqual(
            payload,
            {"text": "Xin chào", "voice_id": "Minh Đức", "speed": 1.5, "format": "wav", "language": "zh"},
        )
        self.assertEqual(request.get_header("X-api-key"), "lan-key")
        self.assertEqual(output.read_bytes(), b"RIFF0000WAVE")

    @patch.dict(os.environ, CIT_ENV, clear=True)
    def test_default_voice_follows_gender(self) -> None:
        opener, calls = fake_cit()
        with patch("infrastructure.tts_providers.urlopen", side_effect=opener):
            synthesize_cit("Chào", Path(self.temp.name) / "a.wav", voice_type="male")
        self.assertEqual(json.loads(calls[-1].data)["voice_id"], "Minh Đức")

    @patch.dict(os.environ, CIT_ENV, clear=True)
    def test_unreachable_app_lets_dubbing_fall_back(self) -> None:
        with patch("infrastructure.tts_providers.urlopen", side_effect=URLError("refused")):
            with self.assertRaises(TTSProviderUnavailable) as raised:
                synthesize_cit("Chào", Path(self.temp.name) / "a.wav", voice_key="Minh Đức")
        self.assertEqual(tts_account_blocked(raised.exception), "không kết nối được")

    @patch.dict(os.environ, CIT_ENV, clear=True)
    def test_http_errors_keep_status(self) -> None:
        error = HTTPError("http://cit.local", 401, "unauthorized", {}, BytesIO(b"bad key"))
        with patch("infrastructure.tts_providers.urlopen", side_effect=error):
            with self.assertRaises(TTSProviderHTTPError) as raised:
                synthesize_cit("Chào", Path(self.temp.name) / "a.wav", voice_key="Minh Đức")
        self.assertEqual(raised.exception.status, 401)
        self.assertIsNotNone(tts_account_blocked(raised.exception))

    @patch.dict(os.environ, CIT_ENV, clear=True)
    def test_transient_server_error_is_retried_once(self) -> None:
        error = HTTPError(
            "http://cit.local/api/tts/generate",
            500,
            "server error",
            {"Content-Type": "application/json"},
            BytesIO('{"detail":"Không tạo được dữ liệu âm thanh"}'.encode()),
        )
        opener, calls = fake_cit()
        attempts = [error]

        def flaky_open(request, timeout=None):
            if attempts:
                raise attempts.pop()
            return opener(request, timeout)

        output = Path(self.temp.name) / "retry.wav"
        with patch("infrastructure.tts_providers.time.sleep") as sleep, \
                patch("infrastructure.tts_providers.urlopen", side_effect=flaky_open):
            synthesize_cit("Chào", output, voice_key="Minh Đức")

        sleep.assert_called_once_with(tts_providers.CIT_RETRY_BASE_SECONDS)
        self.assertEqual(len(calls), 1)
        self.assertEqual(output.read_bytes(), b"RIFF0000WAVE")

    @patch.dict(os.environ, CIT_ENV, clear=True)
    def test_persistent_server_error_is_reported_after_one_retry(self) -> None:
        def server_error(*args, **kwargs):
            return HTTPError(
                "http://cit.local/api/tts/generate",
                500,
                "server error",
                {"Content-Type": "application/json"},
                BytesIO('{"detail":"Không tạo được dữ liệu âm thanh"}'.encode()),
            )

        with patch("infrastructure.tts_providers.time.sleep"), \
                patch("infrastructure.tts_providers.urlopen", side_effect=[server_error(), server_error()]) as opened:
            with self.assertRaises(TTSProviderHTTPError) as raised:
                synthesize_cit("Chào", Path(self.temp.name) / "failed.wav", voice_key="Minh Đức")

        self.assertEqual(opened.call_count, 2)
        self.assertEqual(raised.exception.status, 500)
        self.assertIn("Không tạo được dữ liệu âm thanh", raised.exception.detail)

    @patch.dict(os.environ, CIT_ENV, clear=True)
    def test_json_body_is_not_saved_as_audio(self) -> None:
        def opener(request, timeout=None):
            return response(b'{"detail":"model loading"}')

        with patch("infrastructure.tts_providers.urlopen", side_effect=opener):
            with self.assertRaisesRegex(RuntimeError, "không trả về dữ liệu âm thanh"):
                synthesize_cit("Chào", Path(self.temp.name) / "a.wav", voice_key="Minh Đức")

    @patch.dict(os.environ, CIT_ENV, clear=True)
    def test_voice_selection_validates_against_app(self) -> None:
        opener, _ = fake_cit()
        with patch("infrastructure.tts_providers.urlopen", side_effect=opener):
            self.assertEqual(
                resolve_tts_voice_selection("cit", "Ngọc Huyền", ""),
                {"female": "Ngọc Huyền", "male": "Minh Đức"},
            )
            self.assertEqual(
                resolve_tts_voice_selection("cit", "Minh Đức", "Minh Đức"),
                {"female": "Ngọc Huyền", "male": "Minh Đức"},
            )
            with self.assertRaisesRegex(ValueError, "không còn trong ứng dụng"):
                resolve_tts_voice_selection("cit", "Đã xoá", "")
        self.assertEqual(resolve_tts_voice_selection("edge", "x", "y"), {})

    @patch.dict(os.environ, CIT_ENV, clear=True)
    def test_preview_is_short_and_reused_from_disk_after_memory_cache_reset(self) -> None:
        opener, calls = fake_cit()
        cache_dir = Path(self.temp.name) / "preview-cache"
        with patch.dict(os.environ, {**CIT_ENV, "VIDTRANS_TTS_PREVIEW_CACHE_DIR": str(cache_dir)}, clear=True), \
                patch("infrastructure.tts_providers.urlopen", side_effect=opener):
            first = tts_providers.cit_voice_preview("Ngọc Huyền")
            tts_providers._cit_previews.clear()  # Simulate a backend restart.
            second = tts_providers.cit_voice_preview("Ngọc Huyền")

        generated = [request for request in calls if request.full_url.endswith("/api/tts/generate")]
        self.assertEqual(len(generated), 1)
        self.assertEqual(json.loads(generated[0].data)["text"], "Xin chào, mời bạn nghe thử giọng này.")
        self.assertEqual(first, second)
        self.assertEqual(len(list(cache_dir.glob("*.audio"))), 1)

    @patch.dict(os.environ, CIT_ENV, clear=True)
    def test_startup_warmup_prepares_every_voice_only_once(self) -> None:
        opener, calls = fake_cit()
        cache_dir = Path(self.temp.name) / "preview-cache"
        with patch.dict(os.environ, {**CIT_ENV, "VIDTRANS_TTS_PREVIEW_CACHE_DIR": str(cache_dir)}, clear=True), \
                patch("infrastructure.tts_providers.urlopen", side_effect=opener):
            self.assertEqual(tts_providers.warm_cit_voice_previews(), (3, 0))
            tts_providers._cit_previews.clear()
            self.assertEqual(tts_providers.warm_cit_voice_previews(), (3, 0))

        generated = [request for request in calls if request.full_url.endswith("/api/tts/generate")]
        self.assertEqual(len(generated), 3)
        self.assertEqual(len(list(cache_dir.glob("*.audio"))), 3)


if __name__ == "__main__":
    unittest.main()
