from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from infrastructure.credential_cipher import CredentialCipher
from infrastructure.social_credentials import SocialCredentialStore


class EncryptedOAuthAccountStore:
    """OAuth account-store compatibility layer backed by tenant-scoped encrypted DB rows."""

    def __init__(
        self,
        credentials: SocialCredentialStore,
        cipher: CredentialCipher,
        *,
        user_id: str,
        platform: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.credentials = credentials
        self.cipher = cipher
        self.user_id = user_id
        self.platform = platform.upper()
        self.clock = clock
        self._states: dict[str, float] = {}
        self._lock = threading.RLock()

    def new_state(self) -> str:
        state = secrets.token_urlsafe(32)
        with self._lock:
            now = self.clock()
            self._states = {key: expiry for key, expiry in self._states.items() if expiry > now}
            self._states[state] = now + 600
        return state

    def consume_state(self, state: str) -> bool:
        with self._lock:
            return bool(state and self._states.pop(state, 0) > self.clock())

    def accept_consumed_state(self, state: str) -> None:
        """Stage a state already validated and atomically consumed by the durable session store."""
        if not state:
            raise ValueError("OAuth state không hợp lệ")
        with self._lock:
            self._states[state] = self.clock() + 30

    @staticmethod
    def _iso_from_epoch(value: Any) -> str | None:
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return None
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp > 0 else None

    @staticmethod
    def _epoch_from_iso(value: Any) -> float:
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    def save(self, account_id: str, payload: dict[str, Any]) -> None:
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise ValueError("OAuth provider không trả về access token")
        refresh_token = str(payload.get("refresh_token") or "")
        display_name = str(payload.get("name") or payload.get("username") or "")
        scopes_value = payload.get("scope") or payload.get("scopes") or []
        scopes = (
            [part.strip() for part in str(scopes_value).replace(" ", ",").split(",") if part.strip()]
            if isinstance(scopes_value, str)
            else [str(part) for part in scopes_value]
        )
        metadata = {
            key: value for key, value in payload.items()
            if key not in {"access_token", "refresh_token", "expires_at", "refresh_expires_at"}
        }
        self.credentials.upsert_connection(
            user_id=self.user_id,
            platform=self.platform,
            provider_account_id=account_id,
            display_name=display_name,
            access_token_ciphertext=self.cipher.encrypt(access_token),
            refresh_token_ciphertext=self.cipher.encrypt(refresh_token) if refresh_token else None,
            encryption_key_id=self.cipher.key_id,
            scopes=scopes,
            expires_at=self._iso_from_epoch(payload.get("expires_at")),
            refresh_expires_at=self._iso_from_epoch(payload.get("refresh_expires_at")),
            provider_metadata=metadata,
        )

    def _connection(self, account_id: str) -> dict[str, Any] | None:
        for item in self.credentials.list_for_user(user_id=self.user_id, platform=self.platform):
            if item.get("status") == "active" and str(item.get("provider_account_id")) == account_id:
                return item
        return None

    def load(self, account_id: str) -> dict[str, Any] | None:
        connection = self._connection(account_id)
        if connection is None:
            return None
        encrypted = self.credentials.load_encrypted_tokens(
            user_id=self.user_id, connection_id=str(connection["connection_id"])
        )
        access_blob = bytes(encrypted["access_token_ciphertext"])
        if not access_blob:
            return None
        metadata = dict(connection.get("provider_metadata") or {})
        metadata.update({
            "connection_id": str(connection["connection_id"]),
            "account_id": str(connection["provider_account_id"]),
            "name": str(connection.get("display_name") or metadata.get("name") or ""),
            "access_token": self.cipher.decrypt(access_blob),
            "expires_at": self._epoch_from_iso(connection.get("expires_at")),
            "refresh_expires_at": self._epoch_from_iso(connection.get("refresh_expires_at")),
        })
        refresh_blob = encrypted.get("refresh_token_ciphertext")
        if refresh_blob:
            metadata["refresh_token"] = self.cipher.decrypt(bytes(refresh_blob))
        return metadata

    def all(self) -> list[dict[str, Any]]:
        result = []
        for item in self.credentials.list_for_user(user_id=self.user_id, platform=self.platform):
            if item.get("status") != "active":
                continue
            loaded = self.load(str(item["provider_account_id"]))
            if loaded:
                result.append(loaded)
        return result

    def delete(self, account_id: str) -> None:
        connection = self._connection(account_id)
        if connection is not None:
            self.credentials.revoke(
                user_id=self.user_id, connection_id=str(connection["connection_id"])
            )

