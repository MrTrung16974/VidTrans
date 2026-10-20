from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from infrastructure.tts_providers import ensure_provider_configured, normalize_tts_provider, provider_statuses


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


if __name__ == "__main__":
    unittest.main()
