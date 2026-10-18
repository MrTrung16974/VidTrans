import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.fernet import Fernet

from infrastructure.credential_cipher import CredentialCipher
from infrastructure.platform_apps import PlatformAppError, PlatformAppStore, load_or_create_key
from infrastructure.publishers.facebook import FacebookPublisherConfig
from infrastructure.tiktok_publisher import TikTokPublisherConfig

CALLBACK = "https://vidtrans.example/api/v1/facebook-auth/callback"


class PlatformAppStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.db = Path(self.temp.name) / "jobs.sqlite3"
        self.store = PlatformAppStore(self.db, CredentialCipher(Fernet.generate_key().decode()))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_overlay_uses_web_credentials_and_keeps_secret_encrypted(self) -> None:
        base = FacebookPublisherConfig()
        self.assertIs(self.store.overlay("facebook", base), base)
        self.store.save("facebook", client_id="123", client_secret="s3cret", redirect_uri=CALLBACK, updated_by="admin")
        config = self.store.overlay("FACEBOOK", base)
        self.assertEqual((config.client_id, config.client_secret, config.redirect_uri), ("123", "s3cret", CALLBACK))
        self.assertTrue(config.configured)
        with sqlite3.connect(self.db) as connection:
            raw = connection.execute("SELECT client_secret_ciphertext FROM platform_oauth_apps").fetchone()[0]
        self.assertNotIn(b"s3cret", bytes(raw))
        summary = self.store.describe("facebook", config)
        self.assertEqual(summary["source"], "web")
        self.assertNotIn("client_secret", summary)

    def test_blank_secret_keeps_saved_one_and_tiktok_uses_client_key(self) -> None:
        with self.assertRaisesRegex(PlatformAppError, "Secret"):
            self.store.save("facebook", client_id="1", client_secret="", redirect_uri=CALLBACK, updated_by="a")
        self.store.save("facebook", client_id="1", client_secret="first", redirect_uri=CALLBACK, updated_by="a")
        self.store.save("facebook", client_id="2", client_secret="", redirect_uri=CALLBACK, updated_by="a")
        self.assertEqual(self.store.get("facebook")["client_secret"], "first")
        self.store.save("tiktok", client_id="key", client_secret="sec",
                        redirect_uri="https://x.example/api/v1/tiktok-auth/callback", updated_by="a")
        self.assertEqual(self.store.overlay("tiktok", TikTokPublisherConfig()).client_key, "key")
        self.assertTrue(self.store.delete("tiktok"))
        self.assertIsNone(self.store.get("tiktok"))

    def test_rejects_wrong_callback_and_unknown_platform(self) -> None:
        with self.assertRaises(PlatformAppError):
            self.store.save("facebook", client_id="1", client_secret="s", redirect_uri="https://x/other", updated_by="a")
        with self.assertRaises(PlatformAppError):
            self.store.get("myspace")

    def test_key_file_is_created_once(self) -> None:
        path = Path(self.temp.name) / "secrets" / "k.key"
        self.assertEqual(load_or_create_key(path), load_or_create_key(path))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
