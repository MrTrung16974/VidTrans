"""One-time e-mail codes for self-service password reset.

Only an HMAC of each code is stored. Every guess spends an attempt atomically
before the comparison, so parallel requests cannot exceed the attempt budget.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from infrastructure.database import DatabaseTarget, connect, prepare_target

CODE_DIGITS = 6
CODE_TTL_SECONDS = 10 * 60
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
MAX_SENDS_PER_HOUR = 5


class PasswordResetStore:
    def __init__(
        self, database_path: DatabaseTarget, secret: str, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.database_path = prepare_target(database_path)
        self._key = hashlib.sha256(b"vidtrans-password-reset:" + secret.encode()).digest()
        self._clock = clock
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = connect(self.database_path)
        connection.execute("PRAGMA busy_timeout=30000")
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
            db.execute("""
                CREATE TABLE IF NOT EXISTS password_reset_codes (
                    user_id TEXT PRIMARY KEY,
                    code_hash TEXT NOT NULL,
                    expires_at BIGINT NOT NULL,
                    attempts INTEGER NOT NULL,
                    sent_at BIGINT NOT NULL,
                    window_started_at BIGINT NOT NULL,
                    sends_in_window INTEGER NOT NULL
                )
            """)

    def _hash(self, user_id: str, code: str) -> str:
        return hmac.new(self._key, f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()

    def issue(self, user_id: str) -> str | None:
        """Replace any previous code; None while the resend cooldown or hourly cap applies."""
        now = int(self._clock())
        code = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
        with self._db() as db:
            row = db.execute(
                "SELECT sent_at,window_started_at,sends_in_window FROM password_reset_codes WHERE user_id=?",
                (user_id,),
            ).fetchone()
            window_started, sends = now, 0
            if row is not None:
                if now - int(row["sent_at"]) < RESEND_COOLDOWN_SECONDS:
                    return None
                if now - int(row["window_started_at"]) < 3600:
                    window_started, sends = int(row["window_started_at"]), int(row["sends_in_window"])
                if sends >= MAX_SENDS_PER_HOUR:
                    return None
            db.execute("DELETE FROM password_reset_codes WHERE user_id=?", (user_id,))
            db.execute(
                "INSERT INTO password_reset_codes(user_id,code_hash,expires_at,attempts,sent_at,"
                "window_started_at,sends_in_window) VALUES(?,?,?,?,?,?,?)",
                (user_id, self._hash(user_id, code), now + CODE_TTL_SECONDS, 0, now, window_started, sends + 1),
            )
        return code

    def consume(self, user_id: str, code: str) -> bool:
        """Return True exactly once for the current valid code."""
        code = code.strip()
        if len(code) != CODE_DIGITS or not code.isdigit():
            code = ""
        now = int(self._clock())
        with self._db() as db:
            spent = db.execute(
                "UPDATE password_reset_codes SET attempts=attempts+1 "
                "WHERE user_id=? AND attempts<? AND expires_at>?",
                (user_id, MAX_ATTEMPTS, now),
            )
            if spent.rowcount != 1:
                return False
        if not code:
            return False
        with self._db() as db:
            # Matching on the hash in one UPDATE makes the code single-use even under
            # concurrent confirms; the row stays (expired) so the resend limits still apply.
            used = db.execute(
                "UPDATE password_reset_codes SET code_hash='',expires_at=0 "
                "WHERE user_id=? AND code_hash=? AND expires_at>?",
                (user_id, self._hash(user_id, code), now),
            )
            return used.rowcount == 1
