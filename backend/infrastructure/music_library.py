"""Shared background-music library managed by admins.

Admins upload tracks once; every user can pick one for dubbing (mode 3) instead of
uploading their own file. Metadata lives in the database, the audio files in
``storage_dir`` named by track id, so a title change never moves a file.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from infrastructure.database import connect, prepare_target

MUSIC_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg"}
MAX_MUSIC_BYTES = 30 * 1024 * 1024
MAX_TITLE_CHARS = 120
MAX_TRACKS = 500
MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}


class MusicLibraryError(ValueError):
    pass


class MusicTrackNotFound(MusicLibraryError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def probe_audio_duration(path: Path, ffprobe: str = "ffprobe") -> float:
    """Duration in seconds of the first audio stream; raises if there is none."""
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        payload = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise MusicLibraryError("Không đọc được file âm thanh") from exc
    if not payload.get("streams"):
        raise MusicLibraryError("File không chứa âm thanh")
    try:
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise MusicLibraryError("File âm thanh không có thời lượng hợp lệ")
    return duration


class MusicLibraryStore:
    def __init__(
        self,
        database_path: Any,
        storage_dir: Path,
        probe: Callable[[Path], float] = probe_audio_duration,
    ) -> None:
        self.database_path = prepare_target(database_path)
        self.storage_dir = storage_dir
        self.probe = probe
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS music_tracks (
                    track_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    duration_seconds REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    created_by TEXT
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
    def _row(row: Any) -> dict[str, Any]:
        return {key: row[key] for key in row.keys()}

    def file_path(self, track: dict[str, Any]) -> Path:
        return self.storage_dir / f"{track['track_id']}{track['extension']}"

    def list(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM music_tracks ORDER BY created_at DESC").fetchall()
        return [self._row(row) for row in rows]

    def get(self, track_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM music_tracks WHERE track_id = ?", (track_id,)).fetchone()
        if row is None:
            raise MusicTrackNotFound("Không tìm thấy bản nhạc")
        return self._row(row)

    def create(self, *, title: str, file_name: str, data: bytes, created_by: str | None) -> dict[str, Any]:
        extension = Path(file_name or "").suffix.lower()
        if extension not in MUSIC_EXTENSIONS:
            raise MusicLibraryError("Nhạc nền phải là mp3, m4a, aac, wav hoặc ogg")
        if not data:
            raise MusicLibraryError("File nhạc trống")
        if len(data) > MAX_MUSIC_BYTES:
            raise MusicLibraryError(f"File nhạc vượt quá {MAX_MUSIC_BYTES // (1024 * 1024)} MB")
        title = " ".join((title or Path(file_name).stem).split())[:MAX_TITLE_CHARS]
        if not title:
            raise MusicLibraryError("Hãy đặt tên cho bản nhạc")
        with self._db() as db:
            count = db.execute("SELECT COUNT(*) AS n FROM music_tracks").fetchone()["n"]
        if count >= MAX_TRACKS:
            raise MusicLibraryError(f"Thư viện đã đủ {MAX_TRACKS} bản nhạc; hãy xoá bớt trước")
        track_id = uuid.uuid4().hex[:12]
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        path = self.storage_dir / f"{track_id}{extension}"
        path.write_bytes(data)
        try:
            duration = self.probe(path)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        track = {
            "track_id": track_id,
            "title": title,
            "file_name": Path(file_name).name[:200],
            "extension": extension,
            "size_bytes": len(data),
            "duration_seconds": round(duration, 2),
            "created_at": utc_now(),
            "created_by": created_by,
        }
        try:
            with self._db() as db:
                db.execute(
                    "INSERT INTO music_tracks (track_id, title, file_name, extension, size_bytes,"
                    " duration_seconds, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple(track.values()),
                )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return track

    def rename(self, track_id: str, title: str) -> dict[str, Any]:
        title = " ".join((title or "").split())[:MAX_TITLE_CHARS]
        if not title:
            raise MusicLibraryError("Hãy đặt tên cho bản nhạc")
        self.get(track_id)
        with self._db() as db:
            db.execute("UPDATE music_tracks SET title = ? WHERE track_id = ?", (title, track_id))
        return self.get(track_id)

    def delete(self, track_id: str) -> dict[str, Any]:
        track = self.get(track_id)
        with self._db() as db:
            db.execute("DELETE FROM music_tracks WHERE track_id = ?", (track_id,))
        # Jobs copy the file into their own upload dir, so deleting never breaks a queued job.
        self.file_path(track).unlink(missing_ok=True)
        return track
