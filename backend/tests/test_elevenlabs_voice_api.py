import asyncio
import inspect
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException

import main
from application.job_service import JobService
from infrastructure.job_store import SQLiteJobStore
from infrastructure.tts_providers import elevenlabs_voices
from tests.test_api_tenant_isolation import account, request_for, response_json

VOICE_ENV = {
    "VIDTRANS_ELEVENLABS_API_KEY": "secret",
    "VIDTRANS_ELEVENLABS_VOICES": "Nữ kể chuyện|female|f-1\nNữ trẻ|female|f-2\nNam trầm|male|m-1",
}


class ElevenLabsVoiceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        for name in ("outputs", "uploads", "work"):
            (root / name).mkdir()
        self.service = JobService(SQLiteJobStore(root / "jobs.sqlite3"))
        governance = Mock()
        governance.overview.return_value = {"remaining_minutes": 30}
        self.patches = [
            patch.object(main, "JOB_SERVICE", self.service),
            patch.object(main, "OUTPUT_DIR", root / "outputs"),
            patch.object(main, "UPLOAD_DIR", root / "uploads"),
            patch.object(main, "WORK_DIR", root / "work"),
            patch.object(main, "TENANT_GOVERNANCE", governance),
            patch.object(main, "notify_scheduler"),
            patch.dict(os.environ, VOICE_ENV),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp.cleanup()

    def create_batch(self, **overrides):
        values = {}
        for name, parameter in inspect.signature(main.create_batch_endpoint).parameters.items():
            if name == "request":
                # ElevenLabs needs a paid plan; this test is about voice selection.
                values[name] = request_for({**account("user-a"), "plan": "pro"}, "POST")
            elif name == "source_links":
                values[name] = "https://youtu.be/dQw4w9WgXcQ"
            else:
                default = parameter.default
                values[name] = getattr(default, "default", default)
        values.update(mode=2, tts_provider="elevenlabs", **overrides)
        return asyncio.run(main.create_batch_endpoint(**values))

    def test_selected_voices_are_stored_on_each_job(self) -> None:
        keys = {voice.label: voice.key for voice in elevenlabs_voices()}

        payload = response_json(self.create_batch(elevenlabs_female_voice=keys["Nữ trẻ"]))

        job = self.service.get(payload["jobs"][0]["job_id"])
        self.assertEqual(
            job["resume_request"]["tts_voices"],
            {"female": keys["Nữ trẻ"], "male": keys["Nam trầm"]},
        )

    def test_unknown_voice_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as rejected:
            self.create_batch(elevenlabs_male_voice="v-removed")

        self.assertEqual(rejected.exception.status_code, 400)


    def test_preview_endpoint_returns_audio_and_maps_errors(self) -> None:
        key = elevenlabs_voices()[0].key
        with patch.object(main, "elevenlabs_voice_preview", return_value=(b"ID3", "audio/mpeg")):
            response = main.elevenlabs_voice_preview_endpoint(key)
        self.assertEqual(response.body, b"ID3")
        self.assertEqual(response.media_type, "audio/mpeg")

        with self.assertRaises(HTTPException) as missing:
            main.elevenlabs_voice_preview_endpoint("v-unknown")
        self.assertEqual(missing.exception.status_code, 404)

        with patch.object(main, "elevenlabs_voice_preview", side_effect=RuntimeError("down")):
            with self.assertRaises(HTTPException) as upstream:
                main.elevenlabs_voice_preview_endpoint(key)
        self.assertEqual(upstream.exception.status_code, 502)

if __name__ == "__main__":
    unittest.main()
