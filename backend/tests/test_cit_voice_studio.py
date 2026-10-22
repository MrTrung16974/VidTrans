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
        self.addCleanup(tts_providers._cit_cache.clear)
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

    @patch.dict(os.environ, {**CIT_ENV, "VIDTRANS_CIT_VOICE_API_KEY": "lan-key"}, clear=True)
    def test_generate_sends_voice_speed_language_and_key(self) -> None:
        opener, calls = fake_cit()
        output = Path(self.temp.name) / "out.mp3"
        with patch("infrastructure.tts_providers.urlopen", side_effect=opener):
            synthesize_cit("Xin chào", output, voice_key="Minh Đức", speed=2.0, language="zh-CN")
        request = calls[-1]
        payload = json.loads(request.data)
        self.assertEqual(payload, {"text": "Xin chào", "voice_id": "Minh Đức", "speed": 1.5, "language": "zh"})
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
            with self.assertRaisesRegex(ValueError, "không còn trong ứng dụng"):
                resolve_tts_voice_selection("cit", "Đã xoá", "")
        self.assertEqual(resolve_tts_voice_selection("edge", "x", "y"), {})


if __name__ == "__main__":
    unittest.main()
