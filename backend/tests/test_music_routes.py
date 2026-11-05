"""Background-music library: admin upload/rename/delete, users list and preview."""
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from application.music_routes import MusicRouteDependencies, create_music_router
from infrastructure.music_library import MusicLibraryError, MusicLibraryStore


def fake_probe(path: Path) -> float:
    if path.read_bytes().startswith(b"BAD"):
        raise MusicLibraryError("File không chứa âm thanh")
    return 42.5


class MusicRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = MusicLibraryStore(root / "db.sqlite3", root / "music", probe=fake_probe)
        self.audits: list[dict] = []
        self.role = "admin"

        def require_admin(request: Request) -> dict:
            if self.role not in {"owner", "admin"}:
                raise HTTPException(status_code=403, detail="forbidden")
            return {"user_id": "admin-1", "role": self.role}

        app = FastAPI()
        app.include_router(create_music_router(MusicRouteDependencies(
            store=self.store,
            current_account=lambda request: {"user_id": "user-1"},
            require_admin=require_admin,
            audit=lambda **event: self.audits.append(event),
        )))
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def upload(self, name: str = "Lofi chill.mp3", data: bytes = b"ID3audio", title: str = "") -> object:
        return self.client.post(
            "/api/v1/admin/music-tracks",
            data={"title": title},
            files={"file": (name, data, "audio/mpeg")},
        )

    def test_admin_uploads_track_and_users_list_and_play_it(self) -> None:
        response = self.upload(title="  Nhạc   vui ")
        self.assertEqual(response.status_code, 201, response.text)
        track = response.json()
        self.assertEqual(track["title"], "Nhạc vui")
        self.assertEqual(track["duration_seconds"], 42.5)
        self.assertEqual(self.audits[-1]["action"], "admin.music_track.create")

        items = self.client.get("/api/v1/music-tracks").json()["items"]
        self.assertEqual([item["track_id"] for item in items], [track["track_id"]])
        audio = self.client.get(track["audio_url"])
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.content, b"ID3audio")
        self.assertEqual(audio.headers["content-type"], "audio/mpeg")

    def test_title_defaults_to_file_name(self) -> None:
        self.assertEqual(self.upload().json()["title"], "Lofi chill")

    def test_rejects_wrong_extension_and_non_audio(self) -> None:
        self.assertEqual(self.upload(name="x.exe").status_code, 400)
        response = self.upload(data=b"BAD")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list((Path(self.temp.name) / "music").iterdir()), [])
        self.assertEqual(self.store.list(), [])

    def test_non_admin_cannot_manage(self) -> None:
        self.role = "editor"
        self.assertEqual(self.upload().status_code, 403)

    def test_rename_and_delete(self) -> None:
        track = self.upload().json()
        renamed = self.client.post(f"/api/v1/admin/music-tracks/{track['track_id']}", data={"title": "Mới"})
        self.assertEqual(renamed.json()["title"], "Mới")
        path = self.store.file_path(self.store.get(track["track_id"]))
        self.assertTrue(path.is_file())
        self.assertEqual(self.client.delete(f"/api/v1/admin/music-tracks/{track['track_id']}").status_code, 200)
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get(track["audio_url"]).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/v1/admin/music-tracks/{track['track_id']}").status_code, 404)

    def test_category_and_title_come_from_the_file_name(self) -> None:
        cases = {
            "[Buồn] Mưa rơi_(Official Audio) 320kbps.mp3": ("Mưa rơi", "Buồn / Cảm động"),
            "Lofi Chill Beat for Study.mp3": ("Lofi Chill Beat for Study", "Lofi / Thư giãn"),
            "nhac_hai_huoc_vui_nhon.mp3": ("nhac hai huoc vui nhon", "Hài hước / Vui nhộn"),
            "Epic Battle Trailer Music (No Copyright).mp3": ("Epic Battle Trailer Music", "Hành động / Kịch tính"),
            "古风 古筝 - 青花.mp3": ("古风 古筝 - 青花", "Cổ trang / Trung Hoa"),
            "Bóng Ma Đêm Khuya.mp3": ("Bóng Ma Đêm Khuya", "Kinh dị / Hồi hộp"),
            # Common words that only look like keywords once accents are gone ("mà", "hai").
            "Mà em đâu biết.mp3": ("Mà em đâu biết", "Khác"),
            "Hai mươi năm.mp3": ("Hai mươi năm", "Khác"),
        }
        for name, (title, category) in cases.items():
            with self.subTest(name=name):
                track = self.upload(name=name).json()
                self.assertEqual((track["title"], track["category"]), (title, category))

    def test_an_admin_category_wins_and_can_be_changed(self) -> None:
        response = self.client.post("/api/v1/admin/music-tracks", data={"category": "edm"},
                                    files={"file": ("Piano buồn.mp3", b"ID3audio", "audio/mpeg")})
        track = response.json()
        self.assertEqual(track["category"], "Sôi động / EDM")  # a known mood, matched loosely
        changed = self.client.post(f"/api/v1/admin/music-tracks/{track['track_id']}", data={"category": "Nhạc thiếu nhi"})
        self.assertEqual((changed.json()["title"], changed.json()["category"]), ("Piano buồn", "Nhạc thiếu nhi"))
        listing = self.client.get("/api/v1/music-tracks").json()
        self.assertEqual(listing["categories"][-2:], ["Nhạc thiếu nhi", "Khác"])
        self.assertEqual(self.client.post(f"/api/v1/admin/music-tracks/{track['track_id']}").status_code, 400)

    def test_tracks_from_before_categories_are_filed_by_title(self) -> None:
        import sqlite3
        root = Path(self.temp.name)
        legacy = root / "legacy.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.execute("CREATE TABLE music_tracks (track_id TEXT PRIMARY KEY, title TEXT NOT NULL, file_name TEXT NOT NULL,"
                       " extension TEXT NOT NULL, size_bytes INTEGER NOT NULL, duration_seconds REAL NOT NULL,"
                       " created_at TEXT NOT NULL, created_by TEXT)")
            db.execute("INSERT INTO music_tracks VALUES ('t1', 'Sad piano', 'a.mp3', '.mp3', 1, 1, '2026-01-01', NULL)")
        store = MusicLibraryStore(legacy, root / "music2", probe=fake_probe)
        self.assertEqual(store.get("t1")["category"], "Buồn / Cảm động")


if __name__ == "__main__":
    unittest.main()
