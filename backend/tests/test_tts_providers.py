from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.tts_providers import ensure_provider_configured, normalize_tts_provider, provider_statuses, synthesize_elevenlabs


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


if __name__ == "__main__":
    unittest.main()
