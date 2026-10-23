import asyncio
import inspect
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import main
from domain.plan_entitlements import (
    PlanEntitlementError,
    ensure_comic_settings_allowed,
    ensure_video_providers_allowed,
    provider_allowed,
)
from tests.test_api_tenant_isolation import account, request_for


def on_plan(plan: str, role: str = "editor") -> dict:
    return {**account("user-a", role), "plan": plan}


class PlanEntitlementRulesTests(unittest.TestCase):
    def test_free_plan_gets_only_free_engines(self) -> None:
        free = on_plan("free")
        for provider in ("google", "translategemma", "qwen3"):
            self.assertTrue(provider_allowed(free, "translation", provider))
        for provider in ("edge", "cit", "azure"):
            self.assertTrue(provider_allowed(free, "tts", provider))
        self.assertFalse(provider_allowed(free, "translation", "gemini"))
        self.assertFalse(provider_allowed(free, "tts", "elevenlabs"))

    def test_paid_plans_and_admins_get_gemini_and_elevenlabs(self) -> None:
        for user in (on_plan("creator"), on_plan("pro"), on_plan("enterprise"), on_plan("free", "admin"),
                     on_plan("free", "owner")):
            self.assertTrue(provider_allowed(user, "translation", "gemini"))
            self.assertTrue(provider_allowed(user, "tts", "elevenlabs"))

    def test_subtitle_only_jobs_ignore_the_tts_choice(self) -> None:
        ensure_video_providers_allowed(on_plan("free"), translation_provider="google",
                                       tts_provider="elevenlabs", dubbing=False)
        with self.assertRaisesRegex(PlanEntitlementError, "ElevenLabs"):
            ensure_video_providers_allowed(on_plan("free"), translation_provider="google",
                                           tts_provider="elevenlabs", dubbing=True)

    def test_comic_gemini_and_veo_need_a_paid_plan(self) -> None:
        free = on_plan("free")
        ensure_comic_settings_allowed(free, {"llm_provider": "qwen3", "image_provider": "comfyui",
                                             "video_mode": "comic", "animation_provider": "veo"})
        for settings in ({"llm_provider": "gemini"}, {"image_provider": "gemini"},
                         {"video_mode": "animated", "animation_provider": "veo"}):
            with self.assertRaises(PlanEntitlementError):
                ensure_comic_settings_allowed(free, settings)
        ensure_comic_settings_allowed(on_plan("pro"), {"llm_provider": "gemini", "image_provider": "gemini",
                                                       "video_mode": "animated", "animation_provider": "veo"})


class PlanEntitlementApiTests(unittest.TestCase):
    def create_batch(self, user: dict, **overrides):
        values = {}
        for name, parameter in inspect.signature(main.create_batch_endpoint).parameters.items():
            if name == "request":
                values[name] = request_for(user, "POST")
            elif name == "source_links":
                values[name] = "https://youtu.be/dQw4w9WgXcQ"
            else:
                default = parameter.default
                values[name] = getattr(default, "default", default)
        values.update(overrides)
        return asyncio.run(main.create_batch_endpoint(**values))

    def test_free_user_cannot_start_gemini_or_elevenlabs_jobs(self) -> None:
        for overrides in ({"translation_provider": "gemini"}, {"mode": 2, "tts_provider": "elevenlabs"}):
            with self.assertRaises(HTTPException) as rejected:
                self.create_batch(on_plan("free"), **overrides)
            self.assertEqual(rejected.exception.status_code, 403)
            self.assertIn("Creator, Pro hoặc Enterprise", rejected.exception.detail)

    def test_provider_lists_mark_locked_options(self) -> None:
        with patch.dict("os.environ", {"GEMINI_API_KEY": "", "VIDTRANS_CIT_VOICE_URL": ""}):
            translation = json.loads(main.translation_providers_endpoint(request_for(on_plan("free"))).body)
            tts = json.loads(main.tts_providers_endpoint(request_for(on_plan("pro"))).body)
        by_key = {item["key"]: item for item in translation["providers"]}
        self.assertFalse(by_key["gemini"]["allowed"])
        self.assertTrue(by_key["gemini"]["requires_paid_plan"])
        self.assertTrue(by_key["google"]["allowed"])
        self.assertTrue({item["key"]: item for item in tts["providers"]}["elevenlabs"]["allowed"])


class ComicPlanEntitlementTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests.test_comic_api import ComicApiTests
        self.harness = ComicApiTests("test_validation_errors_are_rejected")
        self.harness.setUp()

    def tearDown(self) -> None:
        self.harness.doCleanups()

    def test_free_user_cannot_create_a_gemini_comic_and_auto_skips_gemini(self) -> None:
        with patch.dict("os.environ", {"GEMINI_API_KEY": "configured"}):
            rejected = self.harness.create(image_provider="gemini")
            options = self.harness.client.get("/api/v1/comic/options").json()
            created = self.harness.create(llm_provider="auto", image_provider="placeholder", auto_render="false")
        self.assertEqual(rejected.status_code, 403)
        gemini_image = next(item for item in options["image_providers"] if item["key"] == "gemini")
        self.assertFalse(gemini_image["allowed"])
        self.assertEqual(created.status_code, 202, created.text)
        job = self.harness.service.get(created.json()["job_id"])
        self.assertIn(job["resume_request"]["settings"]["llm_provider"], {"qwen3", "heuristic"})


if __name__ == "__main__":
    unittest.main()
