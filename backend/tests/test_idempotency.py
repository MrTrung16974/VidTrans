"""Idempotency-Key on create endpoints: a retried request must not queue the work twice."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import main
from infrastructure.job_store import SQLiteJobStore
from tests import test_logo_branding

TTLS = {"pending_ttl_seconds": 900, "ttl_seconds": 86400}


class IdempotencyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SQLiteJobStore(Path(self.temp.name) / "jobs.sqlite3")

    def test_first_claim_wins_until_released_or_completed(self) -> None:
        state, token = self.store.claim_idempotency_key("u", "batches", "k", **TTLS)
        self.assertEqual(state, "claimed")
        self.assertEqual(self.store.claim_idempotency_key("u", "batches", "k", **TTLS), ("pending", None))
        # Keys are per user and per endpoint.
        self.assertEqual(self.store.claim_idempotency_key("other", "batches", "k", **TTLS)[0], "claimed")
        self.assertEqual(self.store.claim_idempotency_key("u", "branding_videos", "k", **TTLS)[0], "claimed")

        self.store.complete_idempotency_key("u", "batches", "k", token, {"batch_id": "b1"})
        self.assertEqual(self.store.claim_idempotency_key("u", "batches", "k", **TTLS), ("done", {"batch_id": "b1"}))

    def test_released_and_expired_claims_can_be_claimed_again(self) -> None:
        _, token = self.store.claim_idempotency_key("u", "batches", "k", **TTLS)
        self.store.release_idempotency_key("u", "batches", "k", token)
        self.assertEqual(self.store.claim_idempotency_key("u", "batches", "k", **TTLS)[0], "claimed")

        later = datetime.now(timezone.utc) + timedelta(seconds=901)
        with patch("infrastructure.job_store.datetime") as clock:
            clock.now.return_value = later
            self.assertEqual(self.store.claim_idempotency_key("u", "batches", "k", **TTLS)[0], "claimed")

    def test_reclaim_takes_over_a_finished_key(self) -> None:
        _, token = self.store.claim_idempotency_key("u", "batches", "k", **TTLS)
        self.store.complete_idempotency_key("u", "batches", "k", token, {"batch_id": "b1"})
        fresh = self.store.reclaim_idempotency_key("u", "batches", "k")
        self.store.complete_idempotency_key("u", "batches", "k", token, {"batch_id": "stale"})
        self.store.complete_idempotency_key("u", "batches", "k", fresh, {"batch_id": "b2"})
        self.assertEqual(self.store.claim_idempotency_key("u", "batches", "k", **TTLS), ("done", {"batch_id": "b2"}))


class BrandingIdempotencyApiTests(unittest.TestCase):
    # Borrow the branding API fixture without collecting its tests twice.
    setUp = test_logo_branding.BrandingApiTests.setUp
    upload_logo = test_logo_branding.BrandingApiTests.upload_logo

    def queue(self, key: str | None, user: str = "a", rights: str = "true"):
        headers = {"x-test-user": user}
        if key:
            headers["Idempotency-Key"] = key
        return self.client.post("/api/v1/branding/videos", headers=headers, data={"rights_confirmed": rights},
                                files={"file": ("clip.mp4", b"fake video", "video/mp4")})

    def test_retry_with_same_key_returns_the_first_job(self) -> None:
        self.upload_logo()
        first = self.queue("retry-1")
        self.assertEqual(first.status_code, 202, first.text)
        again = self.queue("retry-1")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json(), {**first.json(), "duplicate": True})
        self.assertEqual(self.client.get("/api/v1/branding/videos").json()["total"], 1)
        # Without a key, or with another one, a new job is created as before.
        self.assertNotEqual(self.queue(None).json()["job_id"], first.json()["job_id"])
        self.assertNotEqual(self.queue("retry-2").json()["job_id"], first.json()["job_id"])

    def test_key_of_another_user_does_not_replay(self) -> None:
        self.upload_logo()
        self.upload_logo(user="b")
        mine = self.queue("shared").json()
        theirs = self.queue("shared", user="b")
        self.assertEqual(theirs.status_code, 202)
        self.assertNotEqual(theirs.json()["job_id"], mine["job_id"])

    def test_rejected_request_frees_the_key(self) -> None:
        self.upload_logo()
        self.assertEqual(self.queue("k", rights="false").status_code, 400)
        self.assertEqual(self.queue("k").status_code, 202)

    def test_failed_job_is_not_replayed(self) -> None:
        self.upload_logo()
        first = self.queue("k").json()
        self.service.update(first["job_id"], status="failed")
        again = self.queue("k")
        self.assertEqual(again.status_code, 202)
        self.assertNotEqual(again.json()["job_id"], first["job_id"])

    def test_batch_replay_needs_one_job_that_did_not_fail(self) -> None:
        self.service.create("j1", {"status": "failed"}, owner_user_id="user-a")
        self.service.create("j2", {"status": "processing"}, owner_user_id="user-a")
        self.assertTrue(main.batch_replay_valid({"jobs": [{"job_id": "j1"}, {"job_id": "j2"}]}))
        self.assertFalse(main.batch_replay_valid({"jobs": [{"job_id": "j1"}, {"job_id": "gone"}]}))


if __name__ == "__main__":
    unittest.main()
