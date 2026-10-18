from __future__ import annotations

import dataclasses
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from cryptography.fernet import Fernet

from infrastructure.credential_cipher import CredentialCipher


class PlatformAppError(ValueError):
    pass


# One OAuth developer app per platform for the whole VidTrans deployment.
# End users never see these values; they only click "Kết nối" and sign in.
PLATFORM_APPS: dict[str, dict[str, str]] = {
    "TIKTOK": {
        "label": "TikTok", "id_field": "client_key", "id_label": "Client key",
        "callback_path": "/api/v1/tiktok-auth/callback", "env_prefix": "VIDTRANS_TIKTOK",
        "console_url": "https://developers.tiktok.com/apps",
    },
    "FACEBOOK": {
        "label": "Facebook Page", "id_field": "client_id", "id_label": "App ID",
        "callback_path": "/api/v1/facebook-auth/callback", "env_prefix": "VIDTRANS_FACEBOOK",
        "console_url": "https://developers.facebook.com/apps",
    },
    "INSTAGRAM": {
        "label": "Instagram", "id_field": "client_id", "id_label": "Instagram App ID",
        "callback_path": "/api/v1/instagram-auth/callback", "env_prefix": "VIDTRANS_INSTAGRAM",
        "console_url": "https://developers.facebook.com/apps",
    },
    "YOUTUBE": {
        "label": "YouTube", "id_field": "client_id", "id_label": "OAuth Client ID",
        "callback_path": "/api/v1/youtube-auth/callback", "env_prefix": "VIDTRANS_YOUTUBE",
        "console_url": "https://console.cloud.google.com/apis/credentials",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_or_create_key(path: Path) -> str:
    """Return a Fernet key kept beside the database, creating it on first use."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_text(encoding="ascii").strip()
    key = Fernet.generate_key().decode("ascii")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(key)
    return key


class PlatformAppStore:
    """OAuth app credentials managed from the admin page instead of .env."""

    def __init__(self, database_path: Path, cipher: CredentialCipher) -> None:
        self.database_path = Path(database_path)
        self.cipher = cipher
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS platform_oauth_apps (
                    platform TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    client_secret_ciphertext BLOB NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    updated_by TEXT,
                    updated_at TEXT NOT NULL
                )
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _platform(platform: str) -> str:
        key = platform.strip().upper()
        if key not in PLATFORM_APPS:
            raise PlatformAppError("Nền tảng không hỗ trợ cấu hình ứng dụng")
        return key

    def get(self, platform: str) -> dict[str, Any] | None:
        key = self._platform(platform)
        with self._db() as db:
            row = db.execute("SELECT * FROM platform_oauth_apps WHERE platform=?", (key,)).fetchone()
        if row is None:
            return None
        return {
            "client_id": str(row["client_id"]),
            "client_secret": self.cipher.decrypt(bytes(row["client_secret_ciphertext"])),
            "redirect_uri": str(row["redirect_uri"]),
            "updated_by": row["updated_by"],
            "updated_at": row["updated_at"],
        }

    def save(self, platform: str, *, client_id: str, client_secret: str | None,
             redirect_uri: str, updated_by: str) -> None:
        """Store credentials; an empty secret keeps the one already saved."""
        key = self._platform(platform)
        client_id, redirect_uri = client_id.strip(), redirect_uri.strip()
        if not client_id or len(client_id) > 256:
            raise PlatformAppError("App ID / Client ID không hợp lệ")
        if not redirect_uri.startswith(("https://", "http://")) or len(redirect_uri) > 512:
            raise PlatformAppError("Callback URL phải bắt đầu bằng http:// hoặc https://")
        if not redirect_uri.endswith(PLATFORM_APPS[key]["callback_path"]):
            raise PlatformAppError(f"Callback URL phải kết thúc bằng {PLATFORM_APPS[key]['callback_path']}")
        secret = (client_secret or "").strip()
        if not secret:
            current = self.get(key)
            if current is None:
                raise PlatformAppError("Cần nhập App Secret / Client Secret")
            secret = current["client_secret"]
        if len(secret) > 512:
            raise PlatformAppError("App Secret quá dài")
        with self._db() as db:
            db.execute("""
                INSERT INTO platform_oauth_apps(platform,client_id,client_secret_ciphertext,redirect_uri,updated_by,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(platform) DO UPDATE SET client_id=excluded.client_id,
                    client_secret_ciphertext=excluded.client_secret_ciphertext,
                    redirect_uri=excluded.redirect_uri, updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
            """, (key, client_id, self.cipher.encrypt(secret), redirect_uri, updated_by, utc_now()))

    def delete(self, platform: str) -> bool:
        key = self._platform(platform)
        with self._db() as db:
            return db.execute("DELETE FROM platform_oauth_apps WHERE platform=?", (key,)).rowcount > 0

    def overlay(self, platform: str, config: Any) -> Any:
        """Return ``config`` with web-managed credentials taking precedence over .env."""
        stored = self.get(platform)
        if stored is None:
            return config
        return dataclasses.replace(config, **{
            PLATFORM_APPS[self._platform(platform)]["id_field"]: stored["client_id"],
            "client_secret": stored["client_secret"],
            "redirect_uri": stored["redirect_uri"],
        })

    def describe(self, platform: str, effective_config: Any) -> dict[str, Any]:
        key = self._platform(platform)
        meta = PLATFORM_APPS[key]
        stored = self.get(key)
        client_id = str(getattr(effective_config, meta["id_field"], "") or "")
        return {
            "platform": key,
            "label": meta["label"],
            "id_label": meta["id_label"],
            "callback_path": meta["callback_path"],
            "console_url": meta["console_url"],
            "configured": bool(effective_config.configured),
            "source": "web" if stored else ("env" if effective_config.configured else "none"),
            "client_id": client_id,
            "has_secret": bool(getattr(effective_config, "client_secret", "")),
            "redirect_uri": str(getattr(effective_config, "redirect_uri", "") or ""),
            "updated_at": stored["updated_at"] if stored else None,
        }
