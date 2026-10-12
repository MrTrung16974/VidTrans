import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.payments import PaymentConfig, PaymentError, PaymentGatewayClient, PaymentOrderStore


class PaymentOrderStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = TemporaryDirectory()
        self.store = PaymentOrderStore(Path(self.temp_directory.name) / "payments.sqlite3")

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_order_uses_server_catalog_amount_and_is_user_scoped(self) -> None:
        order = self.store.create(user_id="user-a", provider="bank", plan="creator")
        self.assertEqual(order["amount"], 199_000)
        self.assertEqual(order["status"], "pending")
        self.assertEqual(len(self.store.list_for_user("user-a")), 1)
        self.assertEqual(self.store.list_for_user("user-b"), [])
        with self.assertRaisesRegex(PaymentError, "Không tìm thấy"):
            self.store.get(order["order_id"], user_id="user-b")

    def test_rejects_free_enterprise_and_unknown_provider(self) -> None:
        for plan in ("free", "enterprise", "unknown"):
            with self.assertRaises(PaymentError):
                self.store.create(user_id="user-a", provider="bank", plan=plan)
        with self.assertRaises(PaymentError):
            self.store.create(user_id="user-a", provider="cash", plan="pro")

    def test_mark_paid_is_idempotent_by_event_key(self) -> None:
        order = self.store.create(user_id="user-a", provider="bank", plan="pro")
        updated, applied = self.store.mark_paid(
            order["order_id"], event_key="event-1", payload=b"synthetic", provider_transaction_ref="tx-1"
        )
        self.assertTrue(applied)
        self.assertEqual(updated["status"], "paid")
        duplicate, applied_again = self.store.mark_paid(
            order["order_id"], event_key="event-1", payload=b"synthetic", provider_transaction_ref="tx-1"
        )
        self.assertFalse(applied_again)
        self.assertEqual(duplicate["status"], "paid")


class PaymentGatewayClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = PaymentConfig(
            enabled=True,
            public_base_url="https://billing.example.test",
            bank_bin="970436",
            bank_account_no="123456789",
            bank_account_name="VIDTRANS TEST",
            vietqr_client_id="client",
            vietqr_api_key="api-key",
            momo_partner_code="partner",
            momo_access_key="access",
            momo_secret_key="momo-secret",
            momo_endpoint="https://momo.example.test/create",
            zalopay_app_id="2553",
            zalopay_key1="zalo-key-1",
            zalopay_key2="zalo-key-2",
            zalopay_endpoint="https://zalo.example.test/create",
        )
        self.client = PaymentGatewayClient(self.config)

    def test_availability_requires_enabled_and_complete_configuration(self) -> None:
        self.assertEqual(self.config.availability(), {"bank": True, "momo": True, "zalopay": True})

    def test_momo_create_signs_request_without_exposing_secret(self) -> None:
        captured = {}
        self.client._post_json = lambda url, payload: captured.update(url=url, payload=payload) or {
            "resultCode": 0,
            "payUrl": "https://momo.example.test/pay",
        }
        result = self.client.create_momo({"order_id": "order-1", "amount": 199_000, "plan": "creator"})
        self.assertEqual(result["checkout_url"], "https://momo.example.test/pay")
        self.assertEqual(captured["payload"]["requestType"], "captureWallet")
        self.assertNotEqual(captured["payload"]["signature"], self.config.momo_secret_key)
        self.assertNotIn("momo-secret", repr(captured))

    def test_callback_signatures_reject_tampering(self) -> None:
        momo = {
            "amount": 199000,
            "extraData": "",
            "message": "Successful.",
            "orderId": "order-1",
            "orderInfo": "VidTrans creator",
            "orderType": "momo_wallet",
            "partnerCode": "partner",
            "payType": "qr",
            "requestId": "request-1",
            "responseTime": 1,
            "resultCode": 0,
            "transId": 99,
        }
        fields = (
            "accessKey", "amount", "extraData", "message", "orderId", "orderInfo", "orderType",
            "partnerCode", "payType", "requestId", "responseTime", "resultCode", "transId",
        )
        values = {"accessKey": "access", **momo}
        raw = "&".join(f"{field}={values[field]}" for field in fields)
        momo["signature"] = self.client._hmac("momo-secret", raw)
        self.assertTrue(self.client.verify_momo_callback(momo))
        momo["amount"] += 1
        self.assertFalse(self.client.verify_momo_callback(momo))

        data = '{"app_trans_id":"261012_order-1","amount":199000}'
        mac = self.client._hmac("zalo-key-2", data)
        self.assertTrue(self.client.verify_zalopay_callback(data, mac))
        self.assertFalse(self.client.verify_zalopay_callback(data + " ", mac))


if __name__ == "__main__":
    unittest.main()
