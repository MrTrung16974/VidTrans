import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.admin_accounts import AdminAccountStore
from infrastructure.tenant_governance import QuotaExceededError, TenantGovernanceStore


class TenantGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.database = Path(self.temp.name) / "governance.sqlite3"
        self.accounts = AdminAccountStore(self.database, "owner")
        self.owner = self.accounts.list()[0]
        self.store = TenantGovernanceStore(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def test_quota_reserve_adjust_consume_and_release(self):
        user = self.owner["user_id"]
        self.store.reserve(job_id="a", user_id=user, limit_minutes=30, minutes=1)
        self.store.adjust(job_id="a", limit_minutes=30, minutes=20)
        self.store.reserve(job_id="b", user_id=user, limit_minutes=30, minutes=10)
        with self.assertRaises(QuotaExceededError):
            self.store.reserve(job_id="c", user_id=user, limit_minutes=30, minutes=1)
        self.store.consume("a")
        self.store.release("b")
        overview = self.store.overview(user, 30)
        self.assertEqual(overview["consumed_minutes"], 20)
        self.assertEqual(overview["reserved_minutes"], 0)
        self.assertEqual(overview["remaining_minutes"], 10)

    def test_audit_is_filterable(self):
        user = self.owner["user_id"]
        editor = self.accounts.create(email="editor@test.com", display_name="Editor", role="editor")
        editor_id = editor["user_id"]
        self.store.audit(actor_user_id=user, target_user_id=editor_id, action="account.update",
                         resource_type="account", resource_id=editor_id, metadata={"plan": "pro"})
        self.store.audit(actor_user_id=editor_id, target_user_id=None, action="jobs.create",
                         resource_type="job", resource_id="job-1", metadata={})

        items, total = self.store.list_audit(action="account.update")
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["metadata"], {"plan": "pro"})

        items_editor, total_editor = self.store.list_audit(actor_user_id=editor_id)
        self.assertEqual(total_editor, 1)
        self.assertEqual(items_editor[0]["action"], "jobs.create")

        items_target, total_target = self.store.list_audit(target_user_id=editor_id)
        self.assertEqual(total_target, 1)
        self.assertEqual(items_target[0]["action"], "account.update")

    def test_delete_account_safeguard_and_cascades(self):
        from infrastructure.admin_accounts import AdminAccountError
        # Cannot delete owner
        with self.assertRaises(AdminAccountError):
            self.accounts.delete(self.owner["user_id"])

        # Can delete viewer/editor
        viewer = self.accounts.create(email="viewer@test.com", display_name="Viewer", role="viewer")
        viewer_id = viewer["user_id"]
        self.store.reserve(job_id="v1", user_id=viewer_id, limit_minutes=30, minutes=5)
        self.store.audit(actor_user_id=viewer_id, target_user_id=None, action="viewer.action", resource_type="test")

        deleted = self.accounts.delete(viewer_id)
        self.assertEqual(deleted["user_id"], viewer_id)
        with self.assertRaises(AdminAccountError):
            self.accounts.get(viewer_id)


if __name__ == "__main__":
    unittest.main()
