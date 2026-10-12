from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo


PLAN_CATALOG = {
    "free": {"name": "Free", "amount": 0, "minutes": 30},
    "creator": {"name": "Creator", "amount": 199_000, "minutes": 300},
    "pro": {"name": "Pro", "amount": 499_000, "minutes": 1_000},
    "enterprise": {"name": "Enterprise", "amount": None, "minutes": None},
}
PAYMENT_PROVIDERS = {"bank", "momo", "zalopay"}
PAYMENT_STATUSES = {"pending", "paid", "failed", "expired", "cancelled"}


class PaymentError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PaymentConfig:
    enabled: bool
    public_base_url: str
    bank_bin: str
    bank_account_no: str
    bank_account_name: str
    vietqr_client_id: str
    vietqr_api_key: str
    momo_partner_code: str
    momo_access_key: str
    momo_secret_key: str
    momo_endpoint: str
    zalopay_app_id: str
    zalopay_key1: str
    zalopay_key2: str
    zalopay_endpoint: str

    @classmethod
    def from_env(cls) -> "PaymentConfig":
        read = lambda name: os.environ.get(name, "").strip()
        return cls(
            enabled=read("VIDTRANS_PAYMENT_ENABLED").lower() in {"1", "true", "yes", "on"},
            public_base_url=read("VIDTRANS_PUBLIC_BASE_URL").rstrip("/"),
            bank_bin=read("VIDTRANS_BANK_BIN"),
            bank_account_no=read("VIDTRANS_BANK_ACCOUNT_NO"),
            bank_account_name=read("VIDTRANS_BANK_ACCOUNT_NAME"),
            vietqr_client_id=read("VIDTRANS_VIETQR_CLIENT_ID"),
            vietqr_api_key=read("VIDTRANS_VIETQR_API_KEY"),
            momo_partner_code=read("VIDTRANS_MOMO_PARTNER_CODE"),
            momo_access_key=read("VIDTRANS_MOMO_ACCESS_KEY"),
            momo_secret_key=read("VIDTRANS_MOMO_SECRET_KEY"),
            momo_endpoint=read("VIDTRANS_MOMO_ENDPOINT"),
            zalopay_app_id=read("VIDTRANS_ZALOPAY_APP_ID"),
            zalopay_key1=read("VIDTRANS_ZALOPAY_KEY1"),
            zalopay_key2=read("VIDTRANS_ZALOPAY_KEY2"),
            zalopay_endpoint=read("VIDTRANS_ZALOPAY_ENDPOINT"),
        )

    def availability(self) -> dict[str, bool]:
        https_base = self.public_base_url.startswith("https://")
        return {
            "bank": self.enabled and bool(self.bank_bin and self.bank_account_no and self.bank_account_name),
            "momo": self.enabled and https_base and bool(
                self.momo_partner_code and self.momo_access_key and self.momo_secret_key and self.momo_endpoint
            ),
            "zalopay": self.enabled and https_base and bool(
                self.zalopay_app_id and self.zalopay_key1 and self.zalopay_key2 and self.zalopay_endpoint
            ),
        }


class PaymentOrderStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS payment_orders (
                    order_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    plan TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'VND',
                    status TEXT NOT NULL,
                    transfer_content TEXT NOT NULL,
                    provider_order_ref TEXT,
                    provider_transaction_ref TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_payment_orders_user ON payment_orders(user_id, created_at DESC)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS payment_events (
                    event_key TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in row.keys()}

    def create(self, *, user_id: str, provider: str, plan: str) -> dict[str, Any]:
        if provider not in PAYMENT_PROVIDERS:
            raise PaymentError("Phương thức thanh toán không hợp lệ")
        product = PLAN_CATALOG.get(plan)
        if not product or not product["amount"]:
            raise PaymentError("Gói này không thể thanh toán trực tuyến")
        order_id = uuid.uuid4().hex
        now = utc_now()
        transfer_content = f"VIDTRANS {order_id[:10].upper()}"
        with self._db() as db:
            db.execute(
                """INSERT INTO payment_orders(
                    order_id,user_id,provider,plan,amount,currency,status,transfer_content,created_at,updated_at
                ) VALUES(?,?,?,?,?,'VND','pending',?,?,?)""",
                (order_id, user_id, provider, plan, int(product["amount"]), transfer_content, now, now),
            )
        return self.get(order_id, user_id=user_id)

    def get(self, order_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        query = "SELECT * FROM payment_orders WHERE order_id=?"
        values: list[Any] = [order_id]
        if user_id is not None:
            query += " AND user_id=?"
            values.append(user_id)
        with self._db() as db:
            row = db.execute(query, values).fetchone()
        if row is None:
            raise PaymentError("Không tìm thấy đơn thanh toán")
        return self._row(row)

    def list_for_user(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM payment_orders WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, max(1, min(limit, 100))),
            ).fetchall()
        return [self._row(row) for row in rows]

    def set_provider_order_ref(self, order_id: str, provider_order_ref: str) -> dict[str, Any]:
        if not provider_order_ref or len(provider_order_ref) > 200:
            raise PaymentError("Mã đơn nhà cung cấp không hợp lệ")
        with self._db() as db:
            result = db.execute(
                "UPDATE payment_orders SET provider_order_ref=?,updated_at=? WHERE order_id=? AND status='pending'",
                (provider_order_ref, utc_now(), order_id),
            )
        if result.rowcount != 1:
            raise PaymentError("Không thể cập nhật mã đơn nhà cung cấp")
        return self.get(order_id)

    def get_by_provider_ref(self, provider: str, provider_order_ref: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM payment_orders WHERE provider=? AND provider_order_ref=?",
                (provider, provider_order_ref),
            ).fetchone()
        if row is None:
            raise PaymentError("Không tìm thấy đơn thanh toán")
        return self._row(row)

    def mark_paid(self, order_id: str, *, event_key: str, payload: bytes,
                  provider_transaction_ref: str = "") -> tuple[dict[str, Any], bool]:
        digest = hashlib.sha256(payload).hexdigest()
        now = utc_now()
        with self._db() as db:
            existing = db.execute("SELECT 1 FROM payment_events WHERE event_key=?", (event_key,)).fetchone()
            if existing:
                row = db.execute("SELECT * FROM payment_orders WHERE order_id=?", (order_id,)).fetchone()
                if row is None:
                    raise PaymentError("Không tìm thấy đơn thanh toán")
                return self._row(row), False
            row = db.execute("SELECT * FROM payment_orders WHERE order_id=?", (order_id,)).fetchone()
            if row is None:
                raise PaymentError("Không tìm thấy đơn thanh toán")
            if row["status"] != "pending":
                raise PaymentError("Đơn thanh toán không còn ở trạng thái chờ")
            db.execute(
                "INSERT INTO payment_events(event_key,order_id,payload_digest,created_at) VALUES(?,?,?,?)",
                (event_key, order_id, digest, now),
            )
            db.execute(
                "UPDATE payment_orders SET status='paid',provider_transaction_ref=?,updated_at=? WHERE order_id=?",
                (provider_transaction_ref or None, now, order_id),
            )
        return self.get(order_id), True


class PaymentGatewayClient:
    def __init__(self, config: PaymentConfig, *, timeout_seconds: int = 35) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _hmac(key: str, value: str) -> str:
        return hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PaymentError("Không kết nối được nhà cung cấp thanh toán") from exc
        if not isinstance(result, dict):
            raise PaymentError("Phản hồi nhà cung cấp không hợp lệ")
        return result

    def create_momo(self, order: dict[str, Any]) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        order_info = f"VidTrans {order['plan']}"
        redirect_url = f"{self.config.public_base_url}/#billing"
        ipn_url = f"{self.config.public_base_url}/api/v1/billing/callbacks/momo"
        raw = (
            f"accessKey={self.config.momo_access_key}&amount={order['amount']}&extraData="
            f"&ipnUrl={ipn_url}&orderId={order['order_id']}&orderInfo={order_info}"
            f"&partnerCode={self.config.momo_partner_code}&redirectUrl={redirect_url}"
            f"&requestId={request_id}&requestType=captureWallet"
        )
        payload = {
            "partnerCode": self.config.momo_partner_code,
            "requestType": "captureWallet",
            "ipnUrl": ipn_url,
            "redirectUrl": redirect_url,
            "orderId": order["order_id"],
            "amount": order["amount"],
            "orderInfo": order_info,
            "requestId": request_id,
            "extraData": "",
            "lang": "vi",
            "signature": self._hmac(self.config.momo_secret_key, raw),
        }
        result = self._post_json(self.config.momo_endpoint, payload)
        if result.get("resultCode") != 0 or not result.get("payUrl"):
            raise PaymentError(str(result.get("message") or "MoMo từ chối tạo giao dịch"))
        return {"provider_order_ref": order["order_id"], "checkout_url": result["payUrl"]}

    def create_zalopay(self, order: dict[str, Any], user_id: str) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        date_prefix = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%y%m%d")
        app_trans_id = f"{date_prefix}_{order['order_id'][:32]}"
        embed_data = json.dumps(
            {"redirecturl": f"{self.config.public_base_url}/#billing"}, separators=(",", ":")
        )
        items = "[]"
        raw = "|".join(
            [self.config.zalopay_app_id, app_trans_id, user_id[:50], str(order["amount"]), str(now_ms), embed_data, items]
        )
        payload = {
            "app_id": int(self.config.zalopay_app_id),
            "app_user": user_id[:50],
            "app_trans_id": app_trans_id,
            "app_time": now_ms,
            "amount": order["amount"],
            "description": f"VidTrans {order['plan']}",
            "callback_url": f"{self.config.public_base_url}/api/v1/billing/callbacks/zalopay",
            "item": items,
            "embed_data": embed_data,
            "bank_code": "zalopayapp",
            "mac": self._hmac(self.config.zalopay_key1, raw),
        }
        result = self._post_json(self.config.zalopay_endpoint, payload)
        checkout_url = result.get("order_url") or result.get("payment_url")
        if result.get("return_code") != 1 or not checkout_url:
            raise PaymentError(str(result.get("return_message") or "ZaloPay từ chối tạo giao dịch"))
        return {"provider_order_ref": app_trans_id, "checkout_url": checkout_url}

    def verify_momo_callback(self, payload: dict[str, Any]) -> bool:
        fields = (
            "accessKey", "amount", "extraData", "message", "orderId", "orderInfo", "orderType",
            "partnerCode", "payType", "requestId", "responseTime", "resultCode", "transId",
        )
        values = {"accessKey": self.config.momo_access_key, **payload}
        if any(field not in values for field in fields):
            return False
        raw = "&".join(f"{field}={values[field]}" for field in fields)
        expected = self._hmac(self.config.momo_secret_key, raw)
        return hmac.compare_digest(expected, str(payload.get("signature", "")))

    def verify_zalopay_callback(self, data: str, mac: str) -> bool:
        expected = self._hmac(self.config.zalopay_key2, data)
        return hmac.compare_digest(expected, mac)

