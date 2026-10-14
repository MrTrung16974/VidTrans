from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class CredentialCipherError(RuntimeError):
    pass


class CredentialCipher:
    """Authenticated encryption for OAuth credentials stored in the database."""

    def __init__(self, key: str, *, key_id: str = "primary") -> None:
        if not key.strip():
            raise CredentialCipherError("VIDTRANS_CREDENTIAL_ENCRYPTION_KEY đang trống")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise CredentialCipherError("Khóa mã hóa credential không hợp lệ") from exc
        self.key_id = key_id.strip() or "primary"

    @classmethod
    def from_env(cls) -> "CredentialCipher":
        return cls(
            os.environ.get("VIDTRANS_CREDENTIAL_ENCRYPTION_KEY", ""),
            key_id=os.environ.get("VIDTRANS_CREDENTIAL_ENCRYPTION_KEY_ID", "primary"),
        )

    def encrypt(self, value: str) -> bytes:
        if not value:
            raise CredentialCipherError("Không mã hóa token rỗng")
        return self._fernet.encrypt(value.encode("utf-8"))

    def decrypt(self, value: bytes) -> str:
        try:
            return self._fernet.decrypt(value).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise CredentialCipherError("Không giải mã được credential") from exc
