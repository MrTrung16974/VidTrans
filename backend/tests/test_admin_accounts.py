import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.admin_accounts import AdminAccountError, AdminAccountStore


class AdminAccountStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = TemporaryDirectory()
        self.store = AdminAccountStore(Path(self.temp_directory.name) / "accounts.sqlite3", "admin")

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_bootstraps_owner_and_summary(self) -> None:
        accounts = self.store.list()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["email"], "admin@local.vidtrans")
        self.assertEqual(accounts[0]["role"], "owner")
        self.assertEqual(accounts[0]["approval_status"], "approved")
        self.assertEqual(
            self.store.summary(),
            {"total": 1, "approved": 1, "pending": 0, "payment_attention": 0},
        )

    def test_video_retention_days_per_account(self) -> None:
        account = self.store.create(email="ret@example.com", display_name="Ret")
        self.assertIsNone(account["video_retention_days"])
        self.assertEqual(self.store.video_retention_overrides(), {})
        updated = self.store.update(account["user_id"], video_retention_days="7")
        self.assertEqual(updated["video_retention_days"], 7)
        self.store.update(account["user_id"], plan="pro")
        self.assertEqual(self.store.video_retention_overrides(), {account["user_id"]: 7})
        self.store.update(account["user_id"], video_retention_days=0)
        self.assertEqual(self.store.video_retention_overrides(), {account["user_id"]: 0})
        for bad in ("-1", "abc", "99999"):
            with self.assertRaises(AdminAccountError):
                self.store.update(account["user_id"], video_retention_days=bad)
        self.store.update(account["user_id"], video_retention_days=None)
        self.assertEqual(self.store.video_retention_overrides(), {})

    def test_create_search_filter_and_reject_duplicate_email(self) -> None:
        account = self.store.create(
            email="Creator@Example.com", display_name="Nhà sáng tạo", role="editor", plan="creator"
        )
        self.assertEqual(account["email"], "creator@example.com")
        self.assertEqual(account["approval_status"], "pending")
        self.assertEqual(account["payment_status"], "trial")
        self.assertEqual([item["user_id"] for item in self.store.list(query="sáng")], [account["user_id"]])
        self.assertEqual([item["user_id"] for item in self.store.list(status="pending")], [account["user_id"]])
        with self.assertRaisesRegex(AdminAccountError, "Email đã tồn tại"):
            self.store.create(email="CREATOR@example.com", display_name="Trùng")

    def test_password_hash_is_stored_but_can_be_set_without_affecting_public_fields(self) -> None:
        account = self.store.create(
            email="viewer@example.com", display_name="Viewer", password_hash="synthetic-hash"
        )
        stored = self.store.get_by_email("VIEWER@example.com")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["password_hash"], "synthetic-hash")
        self.store.set_password_hash(account["user_id"], "new-synthetic-hash")
        self.assertEqual(self.store.get(account["user_id"])["password_hash"], "new-synthetic-hash")

    def test_updates_access_approval_plan_and_payment_state(self) -> None:
        account = self.store.create(email="user@example.com", display_name="User")
        updated = self.store.update(
            account["user_id"],
            display_name="Editor",
            role="editor",
            approval_status="approved",
            plan="pro",
            payment_status="active",
        )
        self.assertEqual(updated["display_name"], "Editor")
        self.assertEqual(updated["role"], "editor")
        self.assertEqual(updated["approval_status"], "approved")
        self.assertIsNotNone(updated["approved_at"])
        self.assertEqual(updated["plan"], "pro")
        self.assertEqual(updated["payment_status"], "active")

        self.store.update(account["user_id"], payment_status="past_due")
        self.assertEqual(self.store.summary()["payment_attention"], 1)

    def test_owner_cannot_be_downgraded(self) -> None:
        owner = self.store.list(query="admin@local.vidtrans")[0]
        with self.assertRaisesRegex(AdminAccountError, "Không thể hạ quyền owner"):
            self.store.update(owner["user_id"], role="admin")
        with self.assertRaisesRegex(AdminAccountError, "Không thể khóa owner"):
            self.store.update(owner["user_id"], approval_status="suspended")

    def test_rejects_invalid_filter_and_account_values(self) -> None:
        with self.assertRaises(AdminAccountError):
            self.store.list(status="unknown")
        with self.assertRaises(AdminAccountError):
            self.store.create(email="not-an-email", display_name="User")
        with self.assertRaises(AdminAccountError):
            self.store.create(email="user@example.com", display_name="User", role="superuser")


if __name__ == "__main__":
    unittest.main()
