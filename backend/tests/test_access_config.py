import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import main
from domain.plan_entitlements import configure_entitlement_source
from infrastructure.plan_store import PlanStore


def user(role: str = "editor", plan: str = "free") -> dict:
    return {"user_id": f"{role}-{plan}", "email": f"{role}@test.local", "display_name": role,
            "role": role, "plan": plan, "approval_status": "approved"}


class AccessConfigApiTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = PlanStore(Path(temp.name) / "access.sqlite3", cache_seconds=0)
        self.audit = Mock()
        self.user = user()
        original_store = main.PLAN_STORE  # restore this exact store, not the patched one
        self.addCleanup(configure_entitlement_source, original_store)
        for active in (
            patch.object(main, "PLAN_STORE", self.store),
            patch.object(main, "TENANT_GOVERNANCE", Mock(audit=self.audit)),
            patch.object(main, "current_account", lambda request: self.user),
        ):
            active.start()
            self.addCleanup(active.stop)
        configure_entitlement_source(self.store)
        self.client = TestClient(main.app)

    def menu_keys(self, platform: str) -> list[str]:
        response = self.client.get(f"/api/v1/me/menus?platform={platform}")
        self.assertEqual(response.status_code, 200, response.text)
        return [item["key"] for item in response.json()["items"]]

    def test_menus_follow_plan_and_role(self) -> None:
        for plan in ("free", "trial"):
            self.user = user("editor", plan)
            self.assertEqual(self.menu_keys("mobile")[:4], ["translate", "jobs", "upgrade", "profile"])
            # "Cắt clip" comes with every plan.
            self.assertEqual(self.menu_keys("web"), ["create", "clips", "jobs"])
        self.user = user("editor", "pro")
        self.assertEqual(self.menu_keys("mobile")[:4], ["translate", "comic", "jobs", "social"])
        self.assertEqual(self.menu_keys("web"), ["create", "comic", "clips", "jobs", "tiktok"])
        self.user = user("viewer", "pro")
        self.assertEqual(self.menu_keys("web"), ["create", "clips", "jobs"])
        self.user = user("admin", "free")
        self.assertEqual(self.menu_keys("web"), ["create", "comic", "clips", "jobs", "tiktok", "douyin", "admin"])
        self.assertEqual(self.client.get("/api/v1/me/menus?platform=tv").status_code, 422)

    def test_only_admins_read_or_change_the_config(self) -> None:
        self.user = user("editor", "pro")
        self.assertEqual(self.client.get("/api/v1/admin/access-config").status_code, 403)
        denied = self.client.put("/api/v1/admin/plan-entitlements/free/comic_video", json={"enabled": True})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(
            self.client.put("/api/v1/admin/menus/web/comic", json={"label": "x", "sort_order": 1}).status_code, 403
        )

    def test_admin_grants_a_feature_and_the_menu_and_api_follow(self) -> None:
        self.user = user("admin")
        config = self.client.get("/api/v1/admin/access-config").json()
        self.assertEqual(len(config["entitlements"]), len(config["plans"]) * len(config["features"]))
        granted = self.client.put("/api/v1/admin/plan-entitlements/free/comic_video", json={"enabled": True})
        self.assertEqual(granted.status_code, 200, granted.text)
        self.audit.assert_called_once()
        self.assertEqual(self.audit.call_args.kwargs["action"], "plan_entitlement.update")

        self.user = user("editor", "free")
        self.assertIn("comic", self.menu_keys("mobile"))
        self.assertIn("comic_video", self.client.get("/api/v1/me").json()["features"])
        self.user = user("editor", "trial")
        self.assertNotIn("comic", self.menu_keys("mobile"))

        self.user = user("admin")
        bad = self.client.put("/api/v1/admin/plan-entitlements/gold/comic_video", json={"enabled": True})
        self.assertEqual(bad.status_code, 422)

    def test_admin_changes_a_plans_monthly_minutes_and_the_quota_follows(self) -> None:
        self.user = user("editor", "free")
        self.assertEqual(self.client.put("/api/v1/admin/plans/free", json={"monthly_minutes": 60}).status_code, 403)
        self.assertEqual(main.account_plan_limit(self.user), 30)

        self.user = user("admin")
        changed = self.client.put("/api/v1/admin/plans/free", json={"monthly_minutes": 60})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["monthly_minutes"], 60)
        self.assertEqual(main.account_plan_limit(user("editor", "free")), 60)
        self.assertEqual(self.audit.call_args.kwargs["action"], "plan.quota_update")
        self.assertEqual(self.audit.call_args.kwargs["metadata"]["previous_monthly_minutes"], 30)

        unlimited = self.client.put("/api/v1/admin/plans/free", json={"monthly_minutes": None})
        self.assertIsNone(unlimited.json()["monthly_minutes"])
        self.assertIsNone(main.account_plan_limit(user("editor", "free")))
        config = self.client.get("/api/v1/admin/access-config").json()
        self.assertIsNone(next(plan for plan in config["plans"] if plan["key"] == "free")["monthly_minutes"])

    def test_invalid_plan_minutes_are_rejected(self) -> None:
        self.user = user("admin")
        self.assertEqual(self.client.put("/api/v1/admin/plans/gold", json={"monthly_minutes": 10}).status_code, 404)
        self.assertEqual(self.client.put("/api/v1/admin/plans/free", json={"monthly_minutes": -1}).status_code, 422)
        self.assertEqual(self.client.put("/api/v1/admin/plans/free", json={"monthly_minutes": 10**7}).status_code, 422)

    def test_admin_renames_reorders_and_hides_menus(self) -> None:
        self.user = user("admin")
        renamed = self.client.put("/api/v1/admin/menus/mobile/jobs", json={
            "label": "Video của tôi", "short_label": "Video", "sort_order": 5,
            "required_feature": None, "min_role": "viewer", "is_enabled": True,
        })
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(self.audit.call_args.kwargs["action"], "app_menu.update")
        hidden = self.client.put("/api/v1/admin/menus/mobile/upgrade", json={
            "label": "Nâng cấp", "sort_order": 50, "min_role": "viewer", "is_enabled": False,
        })
        self.assertEqual(hidden.status_code, 200, hidden.text)

        self.user = user("editor", "free")
        items = self.client.get("/api/v1/me/menus?platform=mobile").json()["items"]
        self.assertEqual([item["key"] for item in items], ["jobs", "translate", "profile"])
        self.assertEqual(items[0]["short_label"], "Video")

    def test_invalid_menu_edits_are_rejected(self) -> None:
        self.user = user("owner")
        base = {"label": "Quản trị", "sort_order": 60, "min_role": "admin", "is_enabled": True}
        cases = [
            ("web", "admin", {**base, "is_enabled": False}, 422),
            ("web", "admin", {**base, "min_role": "editor"}, 422),
            ("web", "comic", {**base, "required_feature": "teleport"}, 422),
            ("web", "comic", {**base, "min_role": "god"}, 422),
            ("web", "comic", {**base, "label": "  "}, 422),
            ("web", "comic", {**base, "sort_order": 5000}, 422),
            ("web", "nope", base, 404),
        ]
        for platform, key, body, status in cases:
            response = self.client.put(f"/api/v1/admin/menus/{platform}/{key}", json=body)
            self.assertEqual(response.status_code, status, (key, body, response.text))


if __name__ == "__main__":
    unittest.main()
