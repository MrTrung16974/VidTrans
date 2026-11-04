import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from domain.plan_entitlements import configure_entitlement_source
import domain.plan_entitlements as entitlements


def user(plan: str = "free", role: str = "editor") -> dict:
    return {"user_id": f"u-{plan}", "email": "u@test.local", "display_name": "u", "role": role,
            "plan": plan, "approval_status": "approved"}


class FakeTranslator:
    name = "fake"

    def __init__(self, calls: list):
        self.calls = calls

    def translate_lines(self, lines, durations=None):
        self.calls.append(list(lines))
        return [f"vi:{line}" for line in lines]


class OverrideSource:
    def __init__(self, features: dict[str, frozenset[str]]):
        self.features = features

    def features_for(self, plan_key: str) -> frozenset[str]:
        return self.features.get(plan_key, frozenset())


class TextTranslationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = user()
        self.calls: list = []
        self.created: list = []

        def factory(provider, *, source_language, target_language):
            self.created.append((provider, source_language, target_language))
            return FakeTranslator(self.calls)

        for active in (
            patch.object(main, "current_account", lambda request: self.user),
            patch.object(main, "create_translation_provider", factory),
            patch.object(main, "default_translation_provider", lambda: "gemini"),
        ):
            active.start()
            self.addCleanup(active.stop)
        self.addCleanup(configure_entitlement_source, entitlements._source)
        configure_entitlement_source(None)  # seeded defaults
        self.client = TestClient(main.app)

    def post(self, **body):
        return self.client.post("/api/v1/text-translations", json={"source_language": "zh-CN", **body})

    def test_translates_in_order_once_per_distinct_text(self) -> None:
        response = self.post(texts=["你好", " 开会吗 ", "你好", ""])
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["translations"], ["vi:你好", "vi:开会吗", "vi:你好", ""])
        self.assertEqual(self.calls, [["你好", "开会吗"]])
        self.assertEqual(payload["target_language"], "vi")

    def test_free_plan_falls_back_from_a_paid_default_but_cannot_ask_for_it(self) -> None:
        self.assertEqual(self.post(texts=["你好"]).status_code, 200)
        self.assertEqual(self.created[-1][0], "google")
        self.assertEqual(self.post(texts=["你好"], provider="gemini").status_code, 403)
        self.user = user("pro")
        self.assertEqual(self.post(texts=["你好"]).status_code, 200)
        self.assertEqual(self.created[-1][0], "gemini")

    def test_trial_can_use_it_unless_the_admin_switches_it_off(self) -> None:
        self.user = user("trial")
        self.assertEqual(self.post(texts=["你好"]).status_code, 200)
        configure_entitlement_source(OverrideSource({"trial": frozenset()}))
        denied = self.post(texts=["你好"])
        self.assertEqual(denied.status_code, 403)
        self.assertIn("Dịch ảnh chụp màn hình", denied.json()["detail"])

    def test_floating_translator_has_its_own_switch(self) -> None:
        self.user = user("trial")
        configure_entitlement_source(OverrideSource({"trial": frozenset({"image_translation"})}))
        self.assertEqual(self.post(texts=["你好"]).status_code, 200, "Dịch ảnh still works")
        denied = self.post(texts=["你好"], origin="floating")
        self.assertEqual(denied.status_code, 403)
        self.assertIn("Dịch nổi màn hình", denied.json()["detail"])
        configure_entitlement_source(OverrideSource({"trial": frozenset({"screen_translator"})}))
        self.assertEqual(self.post(texts=["你好"], origin="floating").status_code, 200)
        self.assertEqual(self.post(texts=["你好"]).status_code, 403)

    def test_bad_requests_are_rejected(self) -> None:
        self.assertEqual(self.post(texts=[]).status_code, 422)
        self.assertEqual(self.post(texts=["x"], source_language="auto").status_code, 422)
        self.assertEqual(self.post(texts=["x"], source_language="vi").status_code, 422)
        self.assertEqual(self.post(texts=["x"], source_language="klingon").status_code, 422)
        self.assertEqual(self.post(texts=["x" * 1000] * 13).status_code, 413)
        self.assertEqual(self.post(texts=["x"] * 81).status_code, 422)

    def test_translator_outage_is_reported_as_bad_gateway(self) -> None:
        class Broken:
            def translate_lines(self, lines, durations=None):
                raise RuntimeError("down")

        with patch.object(main, "create_translation_provider", lambda *a, **k: Broken()):
            response = self.post(texts=["你好"])
        self.assertEqual(response.status_code, 502)
        self.assertIn("offline", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
