from __future__ import annotations

import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable


class OAuthAccountStore:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.accounts_dir = self.root / "accounts"
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

    def path(self, account_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", account_id):
            raise ValueError("Định danh tài khoản không hợp lệ")
        return self.accounts_dir / f"{account_id}.json"

    def save(self, account_id: str, payload: dict[str, Any]) -> None:
        path = self.path(account_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(path)

    def load(self, account_id: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.path(account_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def all(self) -> list[dict[str, Any]]:
        try:
            paths = sorted(self.accounts_dir.glob("*.json"))
        except OSError:
            return []
        result = []
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                result.append(payload)
        return result

    def delete(self, account_id: str) -> None:
        self.path(account_id).unlink(missing_ok=True)
