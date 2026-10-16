from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.artifact_io import copy_file_atomic, write_json_atomic


class AtomicArtifactTests(unittest.TestCase):
    def test_copies_binary_file_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            target = Path(directory) / "public" / "video.mp4"
            source.write_bytes(b"\x00vidtrans\xff")

            copy_file_atomic(source, target)

            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(list(target.parent.glob(".video.mp4.*.tmp")), [])

    def test_binary_replace_failure_keeps_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            target = Path(directory) / "video.mp4"
            source.write_bytes(b"new")
            target.write_bytes(b"old")

            with patch("infrastructure.artifact_io.os.replace", side_effect=OSError("synthetic")):
                with self.assertRaises(OSError):
                    copy_file_atomic(source, target)

            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(target.parent.glob(".video.mp4.*.tmp")), [])

    def test_writes_unicode_json_and_leaves_no_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "translation.json"
            write_json_atomic(target, {"text": "Xin chào 世界"})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"text": "Xin chào 世界"})
            self.assertEqual(list(target.parent.glob(".translation.json.*.tmp")), [])

    def test_replace_failure_keeps_existing_file_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "artifact.json"
            target.write_text('{"old": true}\n', encoding="utf-8")

            with patch("infrastructure.artifact_io.os.replace", side_effect=OSError("synthetic")):
                with self.assertRaises(OSError):
                    write_json_atomic(target, {"new": True})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"old": True})
            self.assertEqual(list(target.parent.glob(".artifact.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
