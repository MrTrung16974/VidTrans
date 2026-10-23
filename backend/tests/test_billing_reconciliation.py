import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request

import main
from infrastructure.admin_accounts import AdminAccountStore
from infrastructure.payments import PaymentError, PaymentOrderStore


def request_for(account: dict, method: str = "POST") -> Request:
    request = Request({"type": "http", "method": method, "path": "/", "headers": []})
    request.state.account = account
    return request


def body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


class BillingReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        database = Path(self.temp.name) / "app.sqlite3"
        self.accounts = AdminAccountStore(database)
        self.orders = PaymentOrderStore(database)
        self.customer = self.accounts.create(
            email="customer@test.local", display_name="Customer", role="editor", approval_status="approved"
        )
        self.other = self.accounts.create(
            email="other@test.local", display_name="Other", role="editor", approval_status="approved"
        )
        self.admin = self.accounts.create(
            email="admin@test.local", display_name="Admin", role="admin", approval_status="approved"
        )
        self.governance = Mock()
        self.patches = [
            patch.object(main, "PAYMENT_ORDERS", self.orders),
            patch.object(main, "ADMIN_ACCOUNTS", self.accounts),
            patch.object(main, "TENANT_GOVERNANCE", self.governance),
        ]
        for active_patch in self.patches:
            active_patch.start()
        self.order = self.orders.create(user_id=self.customer["user_id"], provider="bank", plan="pro")

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp.cleanup()

    def plan_of(self, user_id: str) -> str:
        return self.accounts.get(user_id)["plan"]

    def test_customer_report_never_activates_a_plan(self) -> None:
        response = main.confirm_billing_order(request_for(self.customer), self.order["order_id"])

        order = body(response)
        self.assertEqual(order["status"], "pending")
        self.assertTrue(order["customer_reported_at"])
        self.assertEqual(self.plan_of(self.customer["user_id"]), "free")
        # Reporting twice keeps the first timestamp.
        again = body(main.confirm_billing_order(request_for(self.customer), self.order["order_id"]))
        self.assertEqual(again["customer_reported_at"], order["customer_reported_at"])

    def test_customer_cannot_report_or_cancel_another_users_order(self) -> None:
        for endpoint in (main.confirm_billing_order, main.cancel_billing_order):
            with self.assertRaises(HTTPException) as caught:
                endpoint(request_for(self.other), self.order["order_id"])
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(self.orders.get(self.order["order_id"])["status"], "pending")

    def test_gateway_orders_cannot_be_reported_manually(self) -> None:
        momo = self.orders.create(user_id=self.customer["user_id"], provider="momo", plan="pro")
        with self.assertRaisesRegex(PaymentError, "tự động"):
            self.orders.report_transfer(momo["order_id"], user_id=self.customer["user_id"])

    def test_admin_lists_reported_orders_first_and_confirms_with_audit(self) -> None:
        older = self.orders.create(user_id=self.other["user_id"], provider="bank", plan="creator")
        self.orders.report_transfer(self.order["order_id"], user_id=self.customer["user_id"])

        listing = body(main.list_billing_orders_for_admin(
            request_for(self.admin, "GET"), status=None, awaiting_review=False, limit=50, offset=0
        ))
        self.assertEqual(listing["total"], 2)
        self.assertEqual(listing["items"][0]["order_id"], self.order["order_id"])
        self.assertEqual(listing["items"][0]["user_email"], "customer@test.local")
        waiting = body(main.list_billing_orders_for_admin(
            request_for(self.admin, "GET"), status=None, awaiting_review=True, limit=50, offset=0
        ))
        self.assertEqual([item["order_id"] for item in waiting["items"]], [self.order["order_id"]])
        self.assertNotIn(older["order_id"], [item["order_id"] for item in waiting["items"]])

        confirmed = body(main.confirm_bank_payment(request_for(self.admin), self.order["order_id"]))
        self.assertEqual(confirmed["status"], "paid")
        self.assertEqual(self.plan_of(self.customer["user_id"]), "pro")
        actions = [call.kwargs["action"] for call in self.governance.audit.call_args_list]
        self.assertIn("billing.order_confirm", actions)

    def test_admin_reject_cancels_with_reason_in_audit(self) -> None:
        rejected = body(main.reject_bank_payment(
            request_for(self.admin), self.order["order_id"], reason="Không thấy giao dịch"
        ))
        self.assertEqual(rejected["status"], "cancelled")
        self.assertEqual(self.plan_of(self.customer["user_id"]), "free")
        audit = self.governance.audit.call_args.kwargs
        self.assertEqual(audit["action"], "billing.order_reject")
        self.assertEqual(audit["target_user_id"], self.customer["user_id"])
        self.assertEqual(audit["metadata"]["reason"], "Không thấy giao dịch")

    def test_reconciliation_endpoints_require_admin(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            main.list_billing_orders_for_admin(
                request_for(self.customer, "GET"), status=None, awaiting_review=False, limit=50, offset=0
            )
        self.assertEqual(caught.exception.status_code, 403)
        for endpoint in (main.confirm_bank_payment, main.reject_bank_payment):
            with self.assertRaises(HTTPException) as caught:
                endpoint(request_for(self.customer), self.order["order_id"])
            self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(self.plan_of(self.customer["user_id"]), "free")


class BillingFrontendTests(unittest.TestCase):
    def test_customer_ui_no_longer_promises_instant_activation(self) -> None:
        web = Path(__file__).resolve().parents[2] / "web"
        html = (web / "index.html").read_text(encoding="utf-8")
        javascript = (web / "js" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("Kích hoạt gói", javascript)
        self.assertIn("chờ đối soát", javascript)
        self.assertIn('data-admin-subtab="billing"', html)
        self.assertIn("/api/v1/admin/billing/orders?", javascript)
        self.assertIn("data-admin-reject-order", javascript)


if __name__ == "__main__":
    unittest.main()
