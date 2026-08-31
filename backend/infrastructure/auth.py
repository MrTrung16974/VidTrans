from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
JWT_ALGORITHM = "HS256"
JWT_LEEWAY_SECONDS = 30


class AuthError(RuntimeError):
    pass


class AuthConfigurationError(AuthError):
    pass


class InvalidTokenError(AuthError):
    pass


class LoginRateLimitError(AuthError):
    pass


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not value or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in value):
        raise ValueError("invalid base64url")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def hash_password(
    password: str,
    *,
    salt: bytes | None = None,
    iterations: int = PASSWORD_ITERATIONS,
) -> str:
    if len(password) < 12:
        raise ValueError("Mật khẩu phải có ít nhất 12 ký tự")
    if iterations < 100_000:
        raise ValueError("PBKDF2 iterations is too low")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PASSWORD_SCHEME}${iterations}${_b64url_encode(salt)}${_b64url_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 5_000_000:
            return False
        salt = _b64url_decode(salt_text)
        expected = _b64url_decode(digest_text)
        if len(salt) < 16 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool = False
    username: str = ""
    password_hash: str = ""
    jwt_secret: str = ""
    issuer: str = "vidtrans"
    audience: str = "vidtrans-api"
    token_ttl_minutes: int = 480
    cookie_name: str = "vidtrans_access_token"
    cookie_secure: bool = False

    @classmethod
    def from_env(cls) -> "AuthConfig":
        enabled = os.environ.get("VIDTRANS_AUTH_ENABLED", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        try:
            ttl = int(os.environ.get("VIDTRANS_JWT_TTL_MINUTES", "480"))
        except ValueError:
            ttl = 480
        return cls(
            enabled=enabled,
            username=os.environ.get("VIDTRANS_AUTH_USERNAME", "").strip(),
            password_hash=os.environ.get("VIDTRANS_AUTH_PASSWORD_HASH", "").strip(),
            jwt_secret=os.environ.get("VIDTRANS_JWT_SECRET", "").strip(),
            issuer=os.environ.get("VIDTRANS_JWT_ISSUER", "vidtrans").strip() or "vidtrans",
            audience=os.environ.get("VIDTRANS_JWT_AUDIENCE", "vidtrans-api").strip() or "vidtrans-api",
            token_ttl_minutes=ttl,
            cookie_name=os.environ.get("VIDTRANS_AUTH_COOKIE_NAME", "vidtrans_access_token").strip()
            or "vidtrans_access_token",
            cookie_secure=os.environ.get("VIDTRANS_AUTH_COOKIE_SECURE", "0").strip().lower()
            in {"1", "true", "yes", "on"},
        )

    def configuration_errors(self) -> list[str]:
        if not self.enabled:
            return []
        errors: list[str] = []
        if not self.username:
            errors.append("VIDTRANS_AUTH_USERNAME đang trống")
        if not self.password_hash or not self.password_hash.startswith(f"{PASSWORD_SCHEME}$"):
            errors.append("VIDTRANS_AUTH_PASSWORD_HASH chưa hợp lệ")
        if len(self.jwt_secret.encode("utf-8")) < 32:
            errors.append("VIDTRANS_JWT_SECRET phải có ít nhất 32 byte")
        if not 5 <= self.token_ttl_minutes <= 10_080:
            errors.append("VIDTRANS_JWT_TTL_MINUTES phải từ 5 đến 10080")
        return errors

    @property
    def configured(self) -> bool:
        return self.enabled and not self.configuration_errors()


class AuthManager:
    def __init__(
        self,
        config: AuthConfig | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config or AuthConfig.from_env()
        self._clock = clock
        self._failed_logins: dict[str, list[float]] = {}
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        errors = self.config.configuration_errors()
        return {
            "enabled": self.config.enabled,
            "configured": self.config.configured,
            "token_ttl_minutes": self.config.token_ttl_minutes if self.config.enabled else None,
            "configuration_errors": errors,
            "message": (
                "Xác thực OAuth2/JWT đang tắt"
                if not self.config.enabled
                else "Xác thực OAuth2/JWT đã sẵn sàng"
                if not errors
                else "Xác thực đã bật nhưng cấu hình chưa hợp lệ"
            ),
        }

    def authenticate(self, username: str, password: str, client_id: str) -> bool:
        self._require_configured()
        self._check_rate_limit(client_id)
        username_ok = hmac.compare_digest(username.encode("utf-8"), self.config.username.encode("utf-8"))
        password_ok = verify_password(password, self.config.password_hash)
        if username_ok and password_ok:
            with self._lock:
                self._failed_logins.pop(client_id, None)
            return True
        self._record_failed_login(client_id)
        return False

    def issue_token(self, subject: str | None = None) -> tuple[str, int]:
        self._require_configured()
        now = int(self._clock())
        ttl_seconds = self.config.token_ttl_minutes * 60
        payload = {
            "sub": subject or self.config.username,
            "scope": "admin",
            "iat": now,
            "nbf": now,
            "exp": now + ttl_seconds,
            "iss": self.config.issuer,
            "aud": self.config.audience,
            "jti": uuid.uuid4().hex,
        }
        header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
        signing_input = ".".join(
            [
                _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")),
                _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")),
            ]
        )
        signature = hmac.new(
            self.config.jwt_secret.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{signing_input}.{_b64url_encode(signature)}", ttl_seconds

    def verify_token(self, token: str) -> dict[str, Any]:
        self._require_configured()
        if not token or len(token) > 8192:
            raise InvalidTokenError("Token không hợp lệ")
        try:
            header_text, payload_text, signature_text = token.split(".")
            header = json.loads(_b64url_decode(header_text).decode("utf-8"))
            payload = json.loads(_b64url_decode(payload_text).decode("utf-8"))
            signature = _b64url_decode(signature_text)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidTokenError("Token không hợp lệ") from exc
        if not isinstance(header, dict) or header.get("alg") != JWT_ALGORITHM or header.get("typ") != "JWT":
            raise InvalidTokenError("Thuật toán JWT không được chấp nhận")
        if not isinstance(payload, dict):
            raise InvalidTokenError("JWT payload không hợp lệ")
        signing_input = f"{header_text}.{payload_text}".encode("ascii")
        expected = hmac.new(
            self.config.jwt_secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidTokenError("Chữ ký JWT không hợp lệ")

        now = self._clock()
        for claim in ("iat", "nbf", "exp"):
            value = payload.get(claim)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidTokenError(f"JWT thiếu claim {claim} hợp lệ")
        if float(payload["exp"]) <= now - JWT_LEEWAY_SECONDS:
            raise InvalidTokenError("JWT đã hết hạn")
        if float(payload["nbf"]) > now + JWT_LEEWAY_SECONDS:
            raise InvalidTokenError("JWT chưa có hiệu lực")
        if float(payload["iat"]) > now + JWT_LEEWAY_SECONDS:
            raise InvalidTokenError("JWT có thời điểm phát hành không hợp lệ")
        if payload.get("iss") != self.config.issuer or payload.get("aud") != self.config.audience:
            raise InvalidTokenError("JWT không dành cho VidTrans")
        if payload.get("sub") != self.config.username or payload.get("scope") != "admin":
            raise InvalidTokenError("JWT không có quyền quản trị")
        return payload

    def _require_configured(self) -> None:
        if not self.config.enabled:
            raise AuthConfigurationError("Xác thực OAuth2/JWT đang tắt")
        errors = self.config.configuration_errors()
        if errors:
            raise AuthConfigurationError("; ".join(errors))

    def _check_rate_limit(self, client_id: str) -> None:
        now = self._clock()
        with self._lock:
            attempts = [stamp for stamp in self._failed_logins.get(client_id, []) if stamp > now - 300]
            self._failed_logins[client_id] = attempts
            if len(attempts) >= 5:
                retry_after = max(1, int(300 - (now - attempts[0])))
                raise LoginRateLimitError(f"Đăng nhập sai quá nhiều. Thử lại sau {retry_after} giây")

    def _record_failed_login(self, client_id: str) -> None:
        with self._lock:
            self._failed_logins.setdefault(client_id, []).append(self._clock())


def _interactive_setup() -> None:
    password = getpass.getpass("Mật khẩu quản trị mới (tối thiểu 12 ký tự): ")
    confirmation = getpass.getpass("Nhập lại mật khẩu: ")
    if not hmac.compare_digest(password, confirmation):
        raise SystemExit("Hai mật khẩu không khớp")
    print("VIDTRANS_AUTH_PASSWORD_HASH=" + hash_password(password))
    print("VIDTRANS_JWT_SECRET=" + secrets.token_urlsafe(48))


if __name__ == "__main__":
    _interactive_setup()
