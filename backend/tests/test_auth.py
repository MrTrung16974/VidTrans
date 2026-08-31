import unittest

from infrastructure.auth import (
    AuthConfig,
    AuthConfigurationError,
    AuthManager,
    InvalidTokenError,
    LoginRateLimitError,
    hash_password,
    verify_password,
)


class MutableClock:
    def __init__(self, value: float = 1_800_000_000) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class AuthTests(unittest.TestCase):
    password = "StrongPass!123"

    def setUp(self) -> None:
        self.clock = MutableClock()
        self.password_hash = hash_password(
            self.password,
            salt=b"0123456789abcdef",
            iterations=100_000,
        )
        self.config = AuthConfig(
            enabled=True,
            username="admin",
            password_hash=self.password_hash,
            jwt_secret="test-secret-that-is-longer-than-thirty-two-bytes",
            issuer="vidtrans-test",
            audience="vidtrans-test-api",
            token_ttl_minutes=5,
        )
        self.manager = AuthManager(self.config, clock=self.clock)

    def test_password_hash_round_trip_and_rejects_wrong_password(self) -> None:
        self.assertTrue(verify_password(self.password, self.password_hash))
        self.assertFalse(verify_password("WrongPassword!123", self.password_hash))
        self.assertFalse(verify_password(self.password, self.password_hash + "x"))

    def test_authenticate_issue_and_verify_jwt(self) -> None:
        self.assertTrue(self.manager.authenticate("admin", self.password, "127.0.0.1"))
        token, ttl = self.manager.issue_token()
        claims = self.manager.verify_token(token)

        self.assertEqual(ttl, 300)
        self.assertEqual(claims["sub"], "admin")
        self.assertEqual(claims["scope"], "admin")
        self.assertEqual(claims["iss"], "vidtrans-test")
        self.assertEqual(claims["aud"], "vidtrans-test-api")

    def test_rejects_tampered_and_expired_tokens(self) -> None:
        token, _ = self.manager.issue_token()
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with self.assertRaises(InvalidTokenError):
            self.manager.verify_token(tampered)

        self.clock.value += 331
        with self.assertRaises(InvalidTokenError):
            self.manager.verify_token(token)

    def test_rate_limits_repeated_failed_logins(self) -> None:
        for _ in range(5):
            self.assertFalse(self.manager.authenticate("admin", "WrongPassword!123", "client-a"))
        with self.assertRaises(LoginRateLimitError):
            self.manager.authenticate("admin", self.password, "client-a")

        self.clock.value += 301
        self.assertTrue(self.manager.authenticate("admin", self.password, "client-a"))

    def test_disabled_auth_cannot_issue_tokens(self) -> None:
        manager = AuthManager(AuthConfig(enabled=False), clock=self.clock)
        self.assertFalse(manager.status()["configured"])
        with self.assertRaises(AuthConfigurationError):
            manager.issue_token()

    def test_configuration_status_never_exposes_secrets(self) -> None:
        status = self.manager.status()
        serialized = repr(status)
        self.assertTrue(status["enabled"])
        self.assertTrue(status["configured"])
        self.assertNotIn(self.password_hash, serialized)
        self.assertNotIn(self.config.jwt_secret, serialized)


if __name__ == "__main__":
    unittest.main()
