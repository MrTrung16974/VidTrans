"""Per-user branding settings (database) and logo image (tenant work directory).

Each account owns one row in ``user_branding_configs`` and one logo file under
``<root>/users/<user>/branding/``. Uploaded images are re-encoded to PNG so the
renderer only ever reads a file this process wrote.
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from domain.branding import BrandingConfig
from infrastructure.database import connect, prepare_target

MAX_LOGO_BYTES = 5 * 1024 * 1024
MAX_LOGO_SIDE = 4096
MIN_LOGO_SIDE = 16


class BrandingError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalise_logo(data: bytes) -> tuple[bytes, int, int, bool]:
    """Validate an uploaded image and return (png bytes, width, height, has transparency)."""
    from PIL import Image, UnidentifiedImageError

    if not data:
        raise BrandingError("File logo trống")
    if len(data) > MAX_LOGO_BYTES:
        raise BrandingError("Logo tối đa 5 MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"PNG", "WEBP", "JPEG"}:
                raise BrandingError("Logo phải là ảnh PNG (khuyến nghị nền trong suốt), WebP hoặc JPEG")
            if max(image.size) > MAX_LOGO_SIDE or min(image.size) < MIN_LOGO_SIDE:
                raise BrandingError(f"Kích thước logo phải từ {MIN_LOGO_SIDE} đến {MAX_LOGO_SIDE} px mỗi cạnh")
            rgba = image.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise BrandingError("File không phải ảnh hợp lệ") from exc
    # Trim fully transparent padding so the size setting matches what is visible.
    bbox = rgba.getchannel("A").getbbox()
    if bbox is None:
        raise BrandingError("Logo hoàn toàn trong suốt")
    rgba = rgba.crop(bbox)
    transparent = rgba.getchannel("A").getextrema()[0] < 255
    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), rgba.width, rgba.height, transparent


class BrandingStore:
    def __init__(self, database_path: Any, user_dir: Callable[[str], Path]) -> None:
        self.database_path = prepare_target(database_path)
        # Resolves (and creates) the tenant directory that holds the user's logo.
        self.user_dir = user_dir
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS user_branding_configs (
                    user_id TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    logo_filename TEXT,
                    logo_width INTEGER,
                    logo_height INTEGER,
                    logo_transparent INTEGER,
                    updated_at TEXT NOT NULL
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
    def _user(user_id: str) -> str:
        user_id = str(user_id or "").strip()
        if not user_id:
            raise BrandingError("Thiếu người dùng")
        return user_id

    def _row(self, user_id: str) -> Any:
        with self._db() as db:
            return db.execute("SELECT * FROM user_branding_configs WHERE user_id=?", (user_id,)).fetchone()

    def _logo_dir(self, user_id: str) -> Path:
        path = self.user_dir(user_id) / "branding"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get(self, user_id: str) -> dict[str, Any]:
        """Settings and logo metadata; defaults for a user who never saved any."""
        user_id = self._user(user_id)
        row = self._row(user_id)
        config = BrandingConfig.model_validate(json.loads(row["config_json"])) if row else BrandingConfig()
        logo = None
        if row and row["logo_filename"]:
            path = self.logo_path(user_id)
            if path is not None:
                logo = {
                    "filename": str(row["logo_filename"]), "width": int(row["logo_width"]),
                    "height": int(row["logo_height"]), "transparent": bool(row["logo_transparent"]),
                }
        return {"config": config, "logo": logo, "updated_at": row["updated_at"] if row else None}

    def save_config(self, user_id: str, config: BrandingConfig) -> dict[str, Any]:
        user_id = self._user(user_id)
        with self._db() as db:
            db.execute("""
                INSERT INTO user_branding_configs(user_id, config_json, updated_at) VALUES(?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET config_json=excluded.config_json, updated_at=excluded.updated_at
            """, (user_id, config.model_dump_json(), utc_now()))
        return self.get(user_id)

    def save_logo(self, user_id: str, data: bytes) -> dict[str, Any]:
        user_id = self._user(user_id)
        png, width, height, transparent = normalise_logo(data)
        # Content-addressed name: browsers and running jobs never see a half-replaced file.
        filename = f"logo-{hashlib.sha256(png).hexdigest()[:16]}.png"
        directory = self._logo_dir(user_id)
        target = directory / filename
        temporary = directory / f".{filename}.tmp"
        temporary.write_bytes(png)
        temporary.replace(target)
        previous = self._row(user_id)
        config_json = previous["config_json"] if previous else BrandingConfig().model_dump_json()
        with self._db() as db:
            db.execute("""
                INSERT INTO user_branding_configs(user_id, config_json, logo_filename, logo_width, logo_height,
                    logo_transparent, updated_at) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET logo_filename=excluded.logo_filename,
                    logo_width=excluded.logo_width, logo_height=excluded.logo_height,
                    logo_transparent=excluded.logo_transparent, updated_at=excluded.updated_at
            """, (user_id, config_json, filename, width, height, int(transparent), utc_now()))
        for stale in directory.glob("logo-*.png"):
            if stale.name != filename:
                stale.unlink(missing_ok=True)
        return self.get(user_id)

    def delete_logo(self, user_id: str) -> dict[str, Any]:
        user_id = self._user(user_id)
        with self._db() as db:
            db.execute("""
                UPDATE user_branding_configs SET logo_filename=NULL, logo_width=NULL, logo_height=NULL,
                    logo_transparent=NULL, updated_at=? WHERE user_id=?
            """, (utc_now(), user_id))
        for stale in self._logo_dir(user_id).glob("logo-*.png"):
            stale.unlink(missing_ok=True)
        return self.get(user_id)

    def logo_path(self, user_id: str) -> Path | None:
        row = self._row(self._user(user_id))
        if not row or not row["logo_filename"]:
            return None
        directory = self._logo_dir(user_id).resolve()
        path = (directory / str(row["logo_filename"])).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            return None
        return path

    def delete_user(self, user_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM user_branding_configs WHERE user_id=?", (self._user(user_id),))
