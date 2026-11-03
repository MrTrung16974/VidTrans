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


if __name__ == "__main__":
    unittest.main()
