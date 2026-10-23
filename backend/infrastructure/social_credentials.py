from __future__ import annotations

import hashlib
import re
import json
import sqlite3

from infrastructure.database import connect, prepare_target
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit


SUPPORTED_PLATFORMS = {
    "TIKTOK", "FACEBOOK", "YOUTUBE", "INSTAGRAM", "THREADS", "PINTEREST", "LINKEDIN"
}
CONNECTION_STATUSES = {"active", "expired", "revoked", "error"}



def is_app_redirect_uri(value: str) -> bool:
    """Absolute URI with a custom scheme (vidtrans://...) or https (universal/app links)."""
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if not scheme or scheme in {"http", "javascript", "data", "file", "vbscript"}:
        return False
    if not re.fullmatch(r"[a-z][a-z0-9+.-]*", scheme) or any(ch in value for ch in "\r\n\\ "):
        return False
    return scheme != "https" or bool(parts.netloc)

class SocialCredentialError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_object(value: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(value or {}), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_list(values: list[str] | tuple[str, ...] | None) -> str:
    return json.dumps(sorted({str(value).strip() for value in values or [] if str(value).strip()}), separators=(",", ":"))


class SocialCredentialStore:
    """Tenant-safe persistence for user-owned social OAuth grants.

    Token arguments are encrypted blobs produced by the credential cipher. This
    repository deliberately never accepts or returns plaintext tokens.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = prepare_target(database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = connect(self.database_path)
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
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
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS social_account_connections (
                    connection_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    provider_account_id TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    token_type TEXT NOT NULL DEFAULT 'Bearer',
                    scopes_json TEXT NOT NULL DEFAULT '[]',
                    access_token_ciphertext BLOB NOT NULL,
                    refresh_token_ciphertext BLOB,
                    encryption_key_id TEXT NOT NULL,
                    expires_at TEXT,
                    refresh_expires_at TEXT,
                    provider_metadata_json TEXT NOT NULL DEFAULT '{}',
                    last_refreshed_at TEXT,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES admin_accounts(user_id) ON DELETE CASCADE,
                    UNIQUE(user_id, platform, provider_account_id),
                    CHECK(platform IN ('TIKTOK','FACEBOOK','YOUTUBE','INSTAGRAM','THREADS','PINTEREST','LINKEDIN')),
                    CHECK(status IN ('active','expired','revoked','error'))
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS social_oauth_sessions (
                    state_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    pkce_verifier_ciphertext BLOB,
                    encryption_key_id TEXT,
                    return_path TEXT NOT NULL DEFAULT '/#tiktok',
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES admin_accounts(user_id) ON DELETE CASCADE,
                    CHECK(platform IN ('TIKTOK','FACEBOOK','YOUTUBE','INSTAGRAM','THREADS','PINTEREST','LINKEDIN'))
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS social_connection_audit_events (
                    event_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    connection_id TEXT,
                    platform TEXT NOT NULL,
                    action TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES admin_accounts(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id) REFERENCES admin_accounts(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(connection_id) REFERENCES social_account_connections(connection_id) ON DELETE SET NULL
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_connections_user_platform "
                "ON social_account_connections(user_id, platform, status)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_connections_expiry "
                "ON social_account_connections(status, expires_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_oauth_sessions_expiry "
                "ON social_oauth_sessions(expires_at, consumed_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_audit_user_created "
                "ON social_connection_audit_events(user_id, created_at DESC)"
            )

    @staticmethod
    def _platform(value: str) -> str:
        platform = value.strip().upper()
        if platform not in SUPPORTED_PLATFORMS:
            raise SocialCredentialError("Nền tảng mạng xã hội không được hỗ trợ")
        return platform

    @staticmethod
    def _public_row(row: sqlite3.Row) -> dict[str, Any]:
        result = {key: row[key] for key in row.keys() if not key.endswith("_ciphertext")}
        result["scopes"] = json.loads(str(result.pop("scopes_json")))
        result["provider_metadata"] = json.loads(str(result.pop("provider_metadata_json")))
        return result

    @staticmethod
    def state_hash(state: str) -> str:
        if not state or len(state) > 1024:
            raise SocialCredentialError("OAuth state không hợp lệ")
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    def create_oauth_session(
        self,
        *,
        user_id: str,
        platform: str,
        state: str,
        expires_at: str,
        pkce_verifier_ciphertext: bytes | None = None,
        encryption_key_id: str | None = None,
        return_path: str = "/#tiktok",
    ) -> None:
        platform = self._platform(platform)
        if not user_id.strip() or not expires_at.strip():
            raise SocialCredentialError("Thiếu user hoặc thời hạn phiên OAuth")
        # A site path, or an app redirect URI that the caller checked against its allowlist.
        is_path = return_path.startswith("/") and not return_path.startswith("//")
        if not (is_path or is_app_redirect_uri(return_path)) or len(return_path) > 500:
            raise SocialCredentialError("Đường dẫn quay lại không hợp lệ")
        with self._db() as db:
            db.execute(
                """
                INSERT INTO social_oauth_sessions(
                    state_hash,user_id,platform,pkce_verifier_ciphertext,encryption_key_id,
                    return_path,expires_at,created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    self.state_hash(state), user_id, platform, pkce_verifier_ciphertext,
                    encryption_key_id, return_path, expires_at, utc_now(),
                ),
            )

    def oauth_return_path(self, *, platform: str, state: str) -> str | None:
        """Where the OAuth callback sends the user back; readable after the session is consumed."""
        try:
            platform = self._platform(platform)
            state_hash = self.state_hash(state)
        except SocialCredentialError:
            return None
        with self._db() as db:
            row = db.execute(
                "SELECT return_path FROM social_oauth_sessions WHERE state_hash=? AND platform=?",
                (state_hash, platform),
            ).fetchone()
        return str(row["return_path"]) if row is not None else None

    def consume_oauth_session(self, *, platform: str, state: str, now: str | None = None) -> dict[str, Any] | None:
        platform = self._platform(platform)
        moment = now or utc_now()
        state_hash = self.state_hash(state)
        with self._db() as db:
            row = db.execute(
                """
                SELECT * FROM social_oauth_sessions
                WHERE state_hash=? AND platform=? AND consumed_at IS NULL AND expires_at>?
                """,
                (state_hash, platform, moment),
            ).fetchone()
            if row is None:
                return None
            updated = db.execute(
                "UPDATE social_oauth_sessions SET consumed_at=? WHERE state_hash=? AND consumed_at IS NULL",
                (moment, state_hash),
            )
            if updated.rowcount != 1:
                return None
        return {key: row[key] for key in row.keys()}

    def upsert_connection(
        self,
        *,
        user_id: str,
        platform: str,
        provider_account_id: str,
        access_token_ciphertext: bytes,
        encryption_key_id: str,
        display_name: str = "",
        refresh_token_ciphertext: bytes | None = None,
        token_type: str = "Bearer",
        scopes: list[str] | tuple[str, ...] | None = None,
        expires_at: str | None = None,
        refresh_expires_at: str | None = None,
        provider_metadata: Mapping[str, Any] | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        platform = self._platform(platform)
        provider_account_id = provider_account_id.strip()
        display_name = display_name.strip()
        if not user_id.strip() or not provider_account_id or len(provider_account_id) > 255:
            raise SocialCredentialError("Thiếu định danh user hoặc tài khoản nền tảng")
        if not access_token_ciphertext or not encryption_key_id.strip():
            raise SocialCredentialError("Token phải được mã hóa trước khi lưu")
        if len(display_name) > 255 or len(token_type) > 64 or len(encryption_key_id) > 128:
            raise SocialCredentialError("Thông tin kết nối vượt quá giới hạn")
        now = utc_now()
        connection_id = uuid.uuid4().hex
        with self._db() as db:
            existing = db.execute(
                "SELECT connection_id FROM social_account_connections WHERE user_id=? AND platform=? AND provider_account_id=?",
                (user_id, platform, provider_account_id),
            ).fetchone()
            if existing is not None:
                connection_id = str(existing["connection_id"])
            db.execute(
                """
                INSERT INTO social_account_connections(
                    connection_id,user_id,platform,provider_account_id,display_name,status,token_type,
                    scopes_json,access_token_ciphertext,refresh_token_ciphertext,encryption_key_id,
                    expires_at,refresh_expires_at,provider_metadata_json,last_refreshed_at,created_at,updated_at
                ) VALUES(?,?,?,?,?,'active',?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id,platform,provider_account_id) DO UPDATE SET
                    display_name=excluded.display_name,status='active',token_type=excluded.token_type,
                    scopes_json=excluded.scopes_json,access_token_ciphertext=excluded.access_token_ciphertext,
                    refresh_token_ciphertext=COALESCE(excluded.refresh_token_ciphertext,social_account_connections.refresh_token_ciphertext),
                    encryption_key_id=excluded.encryption_key_id,expires_at=excluded.expires_at,
                    refresh_expires_at=excluded.refresh_expires_at,
                    provider_metadata_json=excluded.provider_metadata_json,last_refreshed_at=excluded.last_refreshed_at,
                    revoked_at=NULL,updated_at=excluded.updated_at
                """,
                (
                    connection_id, user_id, platform, provider_account_id, display_name, token_type,
                    _json_list(scopes), sqlite3.Binary(access_token_ciphertext),
                    sqlite3.Binary(refresh_token_ciphertext) if refresh_token_ciphertext else None,
                    encryption_key_id, expires_at, refresh_expires_at, _json_object(provider_metadata),
                    now, now, now,
                ),
            )
            self._insert_audit(
                db, user_id=user_id, actor_user_id=actor_user_id or user_id,
                connection_id=connection_id, platform=platform, action="connected",
                metadata={"provider_account_id": provider_account_id},
            )
        return self.get_for_user(user_id=user_id, connection_id=connection_id)

    def list_for_user(self, *, user_id: str, platform: str | None = None) -> list[dict[str, Any]]:
        values: list[Any] = [user_id]
        clause = "WHERE user_id=?"
        if platform:
            clause += " AND platform=?"
            values.append(self._platform(platform))
        with self._db() as db:
            rows = db.execute(
                f"SELECT * FROM social_account_connections {clause} ORDER BY platform,display_name,created_at",
                values,
            ).fetchall()
        return [self._public_row(row) for row in rows]

    def get_for_user(self, *, user_id: str, connection_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM social_account_connections WHERE connection_id=? AND user_id=?",
                (connection_id, user_id),
            ).fetchone()
        if row is None:
            raise SocialCredentialError("Không tìm thấy kết nối mạng xã hội")
        return self._public_row(row)

    def load_encrypted_tokens(self, *, user_id: str, connection_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute(
                """
                SELECT connection_id,user_id,platform,provider_account_id,status,encryption_key_id,
                       access_token_ciphertext,refresh_token_ciphertext,expires_at,refresh_expires_at
                FROM social_account_connections WHERE connection_id=? AND user_id=?
                """,
                (connection_id, user_id),
            ).fetchone()
        if row is None:
            raise SocialCredentialError("Không tìm thấy kết nối mạng xã hội")
        return {key: row[key] for key in row.keys()}

    def revoke(self, *, user_id: str, connection_id: str, actor_user_id: str | None = None) -> dict[str, Any]:
        now = utc_now()
        with self._db() as db:
            row = db.execute(
                "SELECT platform FROM social_account_connections WHERE connection_id=? AND user_id=?",
                (connection_id, user_id),
            ).fetchone()
            if row is None:
                raise SocialCredentialError("Không tìm thấy kết nối mạng xã hội")
            db.execute(
                """
                UPDATE social_account_connections
                SET status='revoked',access_token_ciphertext=X'',refresh_token_ciphertext=NULL,
                    revoked_at=?,updated_at=? WHERE connection_id=? AND user_id=?
                """,
                (now, now, connection_id, user_id),
            )
            self._insert_audit(
                db, user_id=user_id, actor_user_id=actor_user_id or user_id,
                connection_id=connection_id, platform=str(row["platform"]), action="revoked",
            )
        return self.get_for_user(user_id=user_id, connection_id=connection_id)

    @staticmethod
    def _insert_audit(
        db: sqlite3.Connection,
        *,
        user_id: str,
        actor_user_id: str,
        connection_id: str | None,
        platform: str,
        action: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        db.execute(
            """
            INSERT INTO social_connection_audit_events(
                event_id,user_id,actor_user_id,connection_id,platform,action,metadata_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (uuid.uuid4().hex, user_id, actor_user_id, connection_id, platform, action, _json_object(metadata), utc_now()),
        )
