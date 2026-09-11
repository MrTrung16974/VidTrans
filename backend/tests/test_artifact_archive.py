import tempfile
import unittest
import zipfile
from pathlib import Path
from infrastructure.artifact_archive import build_artifact_archive


class ArtifactArchiveTests(unittest.TestCase):
    def test_downloads_have_independent_complete_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "video.mp4"
            source.write_bytes(b"first video")
            first = build_artifact_archive([source], root)
            source.write_bytes(b"second video")
            second = build_artifact_archive([source], root)
            self.assertNotEqual(first, second)
            for path, expected in ((first, b"first video"), (second, b"second video")):
                with zipfile.ZipFile(path) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertEqual(archive.read("video.mp4"), expected)

    def test_failure_removes_partial_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileNotFoundError):
                build_artifact_archive([root / "missing.mp4"], root)
            self.assertEqual(list(root.glob("download-*.zip")), [])
