"""Public contact form: stored, mailed to support, rate-limited, honeypot, admin inbox."""
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from application.contact_routes import (
    CONTACT_LIMIT_PER_CLIENT, CONTACT_WINDOW_SECONDS, ContactRouteDependencies, create_contact_router,
)
from infrastructure.contact_store import ContactMessageStore
from infrastructure.mailer import MailDeliveryError

VALID = {"name": "Nguyễn A", "email": "Khach@Example.com", "phone": "0912 345 678",
         "topic": "billing", "message": "Tôi đã chuyển khoản nhưng gói chưa kích hoạt."}


class FakeMailer:
    configured = True

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    def send(self, **message) -> None:
        if self.fail:
            raise MailDeliveryError("down")
        self.sent.append(message)


class ContactRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = ContactMessageStore(Path(self.temp.name) / "contact.sqlite3")
        self.mailer = FakeMailer()
        self.now = 1000.0
        self.audits: list[dict] = []
        self.role = "admin"

        def require_admin(request: Request) -> dict:
            if self.role not in {"owner", "admin"}:
                raise HTTPException(status_code=403, detail="forbidden")
            return {"user_id": "admin-1", "role": self.role}

        self.deps = ContactRouteDependencies(
            store=self.store, mailer=self.mailer, support_email=lambda: "support@vidtrans.test",
            client_id=lambda request: "1.2.3.4", require_admin=require_admin,
            audit=lambda **event: self.audits.append(event), clock=lambda: self.now,
        )
        app = FastAPI()
        app.include_router(create_contact_router(self.deps))
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_message_is_stored_and_mailed_to_support_with_reply_to(self) -> None:
        response = self.client.post("/api/v1/contact", data=VALID)
        self.assertEqual(response.status_code, 202, response.text)
        items, total = self.store.list()
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["email"], "khach@example.com")
        self.assertEqual(items[0]["status"], "new")
        self.assertEqual(len(self.mailer.sent), 1)
        mail = self.mailer.sent[0]
        self.assertEqual(mail["to"], "support@vidtrans.test")
        self.assertEqual(mail["reply_to"], "khach@example.com")
        self.assertIn("Thanh toán", mail["subject"])
        self.assertIn(VALID["message"], mail["text"])

    def test_mail_failure_does_not_lose_the_message(self) -> None:
        self.deps.mailer = FakeMailer(fail=True)
        self.assertEqual(self.client.post("/api/v1/contact", data=VALID).status_code, 202)
        self.assertEqual(self.store.count_new(), 1)

    def test_honeypot_is_accepted_silently_but_not_stored(self) -> None:
        response = self.client.post("/api/v1/contact", data={**VALID, "website": "http://spam"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.store.count_new(), 0)
        self.assertEqual(self.mailer.sent, [])

    def test_invalid_input_is_rejected(self) -> None:
        for field, value in (("email", "not-an-email"), ("topic", "x"), ("message", "ngắn"), ("phone", "abc")):
            with self.subTest(field=field):
                self.assertEqual(self.client.post("/api/v1/contact", data={**VALID, field: value}).status_code, 422)
        self.assertEqual(self.store.count_new(), 0)

    def test_rate_limit_per_client_and_window(self) -> None:
        for _ in range(CONTACT_LIMIT_PER_CLIENT):
            self.assertEqual(self.client.post("/api/v1/contact", data=VALID).status_code, 202)
        blocked = self.client.post("/api/v1/contact", data=VALID)
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)
        self.now += CONTACT_WINDOW_SECONDS + 1
        self.assertEqual(self.client.post("/api/v1/contact", data=VALID).status_code, 202)

    def test_admin_lists_and_resolves_with_audit(self) -> None:
        self.client.post("/api/v1/contact", data=VALID)
        listing = self.client.get("/api/v1/admin/contact-messages").json()
        self.assertEqual(listing["new_count"], 1)
        message_id = listing["items"][0]["message_id"]
        resolved = self.client.post(f"/api/v1/admin/contact-messages/{message_id}/resolve")
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["status"], "resolved")
        self.assertEqual(resolved.json()["resolved_by"], "admin-1")
        self.assertEqual(self.audits[-1]["action"], "admin.contact_message.resolve")
        self.assertEqual(self.client.get("/api/v1/admin/contact-messages").json()["items"], [])
        self.assertEqual(len(self.client.get("/api/v1/admin/contact-messages?status=resolved").json()["items"]), 1)
        self.assertEqual(self.client.post("/api/v1/admin/contact-messages/missing/resolve").status_code, 404)

    def test_non_admin_cannot_read_inbox(self) -> None:
        self.role = "editor"
        self.assertEqual(self.client.get("/api/v1/admin/contact-messages").status_code, 403)


class ContactWiringTests(unittest.TestCase):
    def test_contact_endpoint_is_public_and_shipped_page_posts_to_it(self) -> None:
        import main
        self.assertIn("/api/v1/contact", main.PUBLIC_AUTH_PATHS)
        self.assertFalse(main.is_protected_path("/api/v1/contact"))
        self.assertTrue(main.is_protected_path("/api/v1/admin/contact-messages"))
        web = Path(__file__).resolve().parents[2] / "web"
        page = (web / "pages" / "contact.html").read_text(encoding="utf-8")
        self.assertIn('id="contactForm"', page)
        self.assertIn('name="website"', page)
        self.assertIn('fetch("/api/v1/contact"', (web / "js" / "contact-form.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
