from __future__ import annotations

import dataclasses
import os
import sqlite3

from infrastructure.database import connect, prepare_target
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from cryptography.fernet import Fernet

from infrastructure.credential_cipher import CredentialCipher


class PlatformAppError(ValueError):
    pass


# Each user registers their own OAuth developer app per platform; .env remains
# the deployment-wide fallback for users who have not entered one.
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
    """Per-user OAuth app credentials entered on the web instead of .env."""

    def __init__(self, database_path: Path, cipher: CredentialCipher) -> None:
        self.database_path = prepare_target(database_path)
        self.cipher = cipher
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS user_platform_oauth_apps (
                    user_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    client_secret_ciphertext BLOB NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, platform)
                )
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = connect(self.database_path)
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

    @staticmethod
    def _user(user_id: str) -> str:
        user_id = str(user_id or "").strip()
        if not user_id:
            raise PlatformAppError("Thiếu người dùng")
        return user_id

    def get(self, user_id: str, platform: str) -> dict[str, Any] | None:
        key, user_id = self._platform(platform), self._user(user_id)
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM user_platform_oauth_apps WHERE user_id=? AND platform=?", (user_id, key)
            ).fetchone()
        if row is None:
            return None
        return {
            "client_id": str(row["client_id"]),
            "client_secret": self.cipher.decrypt(bytes(row["client_secret_ciphertext"])),
            "redirect_uri": str(row["redirect_uri"]),
            "updated_at": row["updated_at"],
        }

    def save(self, user_id: str, platform: str, *, client_id: str, client_secret: str | None,
             redirect_uri: str) -> None:
        """Store credentials; an empty secret keeps the one already saved."""
        key, user_id = self._platform(platform), self._user(user_id)
        client_id, redirect_uri = client_id.strip(), redirect_uri.strip()
        if not client_id or len(client_id) > 256:
            raise PlatformAppError("App ID / Client ID không hợp lệ")
        if not redirect_uri.startswith(("https://", "http://")) or len(redirect_uri) > 512:
            raise PlatformAppError("Callback URL phải bắt đầu bằng http:// hoặc https://")
        if not redirect_uri.endswith(PLATFORM_APPS[key]["callback_path"]):
            raise PlatformAppError(f"Callback URL phải kết thúc bằng {PLATFORM_APPS[key]['callback_path']}")
        secret = (client_secret or "").strip()
        if not secret:
            current = self.get(user_id, key)
            if current is None:
                raise PlatformAppError("Cần nhập App Secret / Client Secret")
            secret = current["client_secret"]
        if len(secret) > 512:
            raise PlatformAppError("App Secret quá dài")
        with self._db() as db:
            db.execute("""
                INSERT INTO user_platform_oauth_apps(user_id,platform,client_id,client_secret_ciphertext,redirect_uri,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(user_id, platform) DO UPDATE SET client_id=excluded.client_id,
                    client_secret_ciphertext=excluded.client_secret_ciphertext,
                    redirect_uri=excluded.redirect_uri, updated_at=excluded.updated_at
            """, (user_id, key, client_id, self.cipher.encrypt(secret), redirect_uri, utc_now()))

    def delete(self, user_id: str, platform: str) -> bool:
        key, user_id = self._platform(platform), self._user(user_id)
        with self._db() as db:
            return db.execute(
                "DELETE FROM user_platform_oauth_apps WHERE user_id=? AND platform=?", (user_id, key)
            ).rowcount > 0

    def delete_user(self, user_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM user_platform_oauth_apps WHERE user_id=?", (self._user(user_id),))

    def overlay(self, user_id: str, platform: str, config: Any) -> Any:
        """Return ``config`` with the user's own app taking precedence over .env."""
        stored = self.get(user_id, platform)
        if stored is None:
            return config
        return dataclasses.replace(config, **{
            PLATFORM_APPS[self._platform(platform)]["id_field"]: stored["client_id"],
            "client_secret": stored["client_secret"],
            "redirect_uri": stored["redirect_uri"],
        })

    def describe(self, user_id: str, platform: str, effective_config: Any) -> dict[str, Any]:
        """Public view of the user's settings; secrets and system values are never returned."""
        key = self._platform(platform)
        meta = PLATFORM_APPS[key]
        stored = self.get(user_id, key)
        return {
            "platform": key,
            "label": meta["label"],
            "id_label": meta["id_label"],
            "callback_path": meta["callback_path"],
            "console_url": meta["console_url"],
            "configured": bool(effective_config.configured),
            "source": "user" if stored else ("system" if effective_config.configured else "none"),
            "client_id": stored["client_id"] if stored else "",
            "has_secret": bool(stored),
            "redirect_uri": stored["redirect_uri"] if stored else "",
            "updated_at": stored["updated_at"] if stored else None,
        }
