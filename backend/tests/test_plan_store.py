import tempfile
import unittest
from pathlib import Path

from domain.plan_entitlements import (
    FEATURES,
    SOURCE_PLATFORM_FEATURES,
    account_features,
    configure_entitlement_source,
    feature_allowed,
    provider_allowed,
)
from infrastructure.plan_store import PlanStore

# What trial and free get out of the box: image and floating translation, and
# downloading from every source platform (each one can be switched off).
BASIC = frozenset({"image_translation", "screen_translator", *SOURCE_PLATFORM_FEATURES.values()})


def on_plan(plan: str, role: str = "editor") -> dict:
    return {"user_id": "u", "role": role, "plan": plan}


class PlanStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "plans.sqlite3"
        self.store = PlanStore(self.path, cache_seconds=0)
        import domain.plan_entitlements as entitlements

        self.addCleanup(configure_entitlement_source, entitlements._source)
        configure_entitlement_source(self.store)

    def test_every_plan_has_an_explicit_row_per_feature(self) -> None:
        matrix = self.store.matrix()
        plans = {"trial", "free", "creator", "pro", "enterprise"}
        self.assertEqual(len(matrix), len(plans) * len(FEATURES))
        for row in matrix:
            paid = row["plan_key"] in {"creator", "pro", "enterprise"}
            self.assertEqual(row["enabled"], paid or row["feature_key"] in BASIC, row)

    def test_trial_and_free_get_only_the_basic_features(self) -> None:
        for plan in ("trial", "free"):
            self.assertEqual(account_features(on_plan(plan)), BASIC)
            self.assertFalse(feature_allowed(on_plan(plan), "novel_import"))
            self.assertFalse(feature_allowed(on_plan(plan), "comic_video"))
            self.assertFalse(feature_allowed(on_plan(plan), "social_publishing"))
            self.assertFalse(provider_allowed(on_plan(plan), "translation", "gemini"))
            self.assertTrue(provider_allowed(on_plan(plan), "translation", "google"))
        self.assertEqual(account_features(on_plan("pro")), frozenset(FEATURES))
        self.assertEqual(account_features(on_plan("free", "admin")), frozenset(FEATURES))

    def test_unknown_plan_is_denied(self) -> None:
        self.assertEqual(account_features(on_plan("legacy-gold")), frozenset())

    def test_database_changes_apply_and_survive_a_restart(self) -> None:
        self.store.set_entitlement("free", "social_publishing", True)
        self.assertTrue(feature_allowed(on_plan("free"), "social_publishing"))
        self.assertFalse(feature_allowed(on_plan("trial"), "social_publishing"))

        reopened = PlanStore(self.path, cache_seconds=0)  # re-seeding must not reset the change
        self.assertIn("social_publishing", reopened.features_for("free"))

    def test_feature_labels_follow_the_code_after_a_restart(self) -> None:
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE plan_features SET name='Nhập chương truyện từ link' WHERE feature_key='novel_import'")
        PlanStore(self.path, cache_seconds=0)
        with sqlite3.connect(self.path) as db:
            name = db.execute("SELECT name FROM plan_features WHERE feature_key='novel_import'").fetchone()[0]
        self.assertEqual(name, FEATURES["novel_import"][0])

    def test_plans_carry_price_quota_and_features(self) -> None:
        plans = {plan["key"]: plan for plan in self.store.plans()}
        self.assertEqual(list(plans), ["trial", "free", "creator", "pro", "enterprise"])
        self.assertEqual(plans["trial"]["monthly_minutes"], 5)
        self.assertFalse(plans["trial"]["is_paid"])
        self.assertEqual(plans["free"]["features"], sorted(BASIC))
        self.assertIn("comic_video", plans["creator"]["features"])
        self.assertIsNone(plans["enterprise"]["price_vnd"])


if __name__ == "__main__":
    unittest.main()
