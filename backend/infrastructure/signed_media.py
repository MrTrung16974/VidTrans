from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import quote


class SignedMediaError(ValueError):
    pass


class SignedMediaUrlService:
    def __init__(
        self,
        output_dir: Path,
        *,
        public_base_url: str,
        secret: str,
        clock=time.time,
    ) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.public_base_url = public_base_url.rstrip("/")
        self.secret = secret.encode("utf-8")
        self._clock = clock

    @property
    def configured(self) -> bool:
        return self.public_base_url.startswith("https://") and len(self.secret) >= 32

    def create_url(self, path: Path, *, ttl_seconds: int = 3600) -> str:
        if not self.configured:
            raise SignedMediaError(
                "Chưa cấu hình HTTPS public URL và signing secret cho Instagram"
            )
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.output_dir) or not resolved.is_file():
            raise SignedMediaError("Video không nằm trong thư mục output được phép")
        expires = int(self._clock()) + max(60, min(ttl_seconds, 86400))
        relative = resolved.relative_to(self.output_dir).as_posix()
        payload = f"{relative}\n{expires}".encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        signature = hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        token = f"{encoded}.{signature}"
        return f"{self.public_base_url}/api/v1/social-media/{quote(token, safe='')}"

    def resolve(self, token: str) -> Path:
        encoded, separator, supplied_signature = token.partition(".")
        if not separator or not encoded or not supplied_signature:
            raise SignedMediaError("Media token không hợp lệ")
        expected = hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied_signature, expected):
            raise SignedMediaError("Media token không hợp lệ")
        try:
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
            filename, expires_raw = decoded.split("\n", 1)
            expires = int(expires_raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SignedMediaError("Media token không hợp lệ") from exc
        if expires < int(self._clock()):
            raise SignedMediaError("Media token đã hết hạn")
        path = (self.output_dir / filename).resolve()
        if not path.is_relative_to(self.output_dir) or not path.is_file():
            raise SignedMediaError("Không tìm thấy media")
        return path

