from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.artifact_io import write_json_atomic
from infrastructure.stage_checkpoint import (
    StageCheckpointStore,
    fingerprint_payload,
    load_or_compute_json,
    sha256_file,
)


class StageCheckpointTests(unittest.TestCase):
    def test_commit_and_load_valid_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = StageCheckpointStore(Path(directory))
            fingerprint = fingerprint_payload({"source": "abc", "model": "base"})
            committed = store.commit_json(
                "transcript",
                fingerprint=fingerprint,
                artifact_name="transcript.checkpoint.json",
                payload={"segments": [{"start": 0, "end": 1, "text": "hello"}]},
                metadata={"source_method": "speech"},
            )

            loaded = store.load_json(
                "transcript",
                fingerprint=fingerprint,
                validator=lambda value: isinstance(value.get("segments"), list),
            )

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.payload, committed.payload)
            self.assertEqual(loaded.metadata["source_method"], "speech")
            self.assertEqual(loaded.checksum, committed.checksum)

    def test_fingerprint_mismatch_is_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = StageCheckpointStore(Path(directory))
            store.commit_json(
                "translation",
                fingerprint="old",
                artifact_name="translation.checkpoint.json",
                payload={"segments": []},
            )
            self.assertIsNone(store.load_json("translation", fingerprint="new"))

    def test_corrupt_artifact_is_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = StageCheckpointStore(root)
            store.commit_json(
                "transcript",
                fingerprint="same",
                artifact_name="transcript.checkpoint.json",
                payload={"segments": []},
            )
            (root / "transcript.checkpoint.json").write_text('{"segments": [', encoding="utf-8")
            self.assertIsNone(store.load_json("transcript", fingerprint="same"))

    def test_missing_or_corrupt_dependency_is_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependency = root / "voice_track.wav"
            dependency.write_bytes(b"voice-v1")
            store = StageCheckpointStore(root)
            committed = store.commit_json(
                "tts",
                fingerprint="same",
                artifact_name="tts.checkpoint.json",
                payload={"version": 1},
                dependency_names=["voice_track.wav"],
            )
            self.assertNotEqual(committed.bundle_checksum, committed.checksum)
            self.assertIsNotNone(store.load_json("tts", fingerprint="same"))

            dependency.write_bytes(b"voice-v2")
            self.assertIsNone(store.load_json("tts", fingerprint="same"))
            dependency.unlink()
            self.assertIsNone(store.load_json("tts", fingerprint="same"))

    def test_rejects_dependency_path_outside_job_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.wav"
            outside.write_bytes(b"audio")
            store = StageCheckpointStore(root / "job")
            with self.assertRaises(ValueError):
                store.commit_json(
                    "audio_mix",
                    fingerprint="same",
                    artifact_name="audio.checkpoint.json",
                    payload={"version": 1},
                    dependency_names=["../outside.wav"],
                )

    def test_invalid_new_dependency_does_not_overwrite_previous_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "voice.wav").write_bytes(b"valid")
            store = StageCheckpointStore(root)
            store.commit_json(
                "tts",
                fingerprint="old",
                artifact_name="tts.checkpoint.json",
                payload={"version": 1, "value": "old"},
                dependency_names=["voice.wav"],
            )

            with self.assertRaisesRegex(ValueError, "does not exist"):
                store.commit_json(
                    "tts",
                    fingerprint="new",
                    artifact_name="tts.checkpoint.json",
                    payload={"version": 1, "value": "new"},
                    dependency_names=["missing.wav"],
                )

            loaded = store.load_json("tts", fingerprint="old")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.payload["value"], "old")

    def test_manifest_write_failure_preserves_previous_fingerprinted_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = StageCheckpointStore(root)
            store.commit_json(
                "render",
                fingerprint="old",
                artifact_name="checkpoints/render/old.json",
                payload={"version": 1, "artifact": "old.mp4"},
            )

            def fail_manifest(path, payload, **kwargs):
                if Path(path) == store.manifest_path:
                    raise OSError("synthetic manifest failure")
                return write_json_atomic(path, payload, **kwargs)

            with patch(
                "infrastructure.stage_checkpoint.write_json_atomic",
                side_effect=fail_manifest,
            ):
                with self.assertRaisesRegex(OSError, "manifest failure"):
                    store.commit_json(
                        "render",
                        fingerprint="new",
                        artifact_name="checkpoints/render/new.json",
                        payload={"version": 1, "artifact": "new.mp4"},
                    )

            loaded = store.load_json("render", fingerprint="old")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.payload["artifact"], "old.mp4")

    def test_corrupt_or_unsupported_manifest_is_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = StageCheckpointStore(root)
            store.manifest_path.write_text("not-json", encoding="utf-8")
            self.assertIsNone(store.load_json("transcript", fingerprint="same"))
            store.manifest_path.write_text(json.dumps({"version": 999, "stages": {}}), encoding="utf-8")
            self.assertIsNone(store.load_json("transcript", fingerprint="same"))

    def test_rejects_artifact_path_outside_job_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = StageCheckpointStore(Path(directory) / "job")
            with self.assertRaises(ValueError):
                store.commit_json(
                    "transcript",
                    fingerprint="same",
                    artifact_name="../outside.json",
                    payload={},
                )

    def test_file_and_payload_hashes_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.bin"
            path.write_bytes(b"vidtrans")
            self.assertEqual(sha256_file(path), sha256_file(path))
            self.assertEqual(
                fingerprint_payload({"b": 2, "a": 1}),
                fingerprint_payload({"a": 1, "b": 2}),
            )

    def test_file_hash_checks_cancellation_between_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.bin"
            path.write_bytes(b"abcdefgh")
            calls = []

            def check_active() -> None:
                calls.append(True)
                if len(calls) == 2:
                    raise RuntimeError("cancelled")

            with self.assertRaises(RuntimeError):
                sha256_file(path, chunk_size=4, check_active=check_active)
            self.assertEqual(len(calls), 2)

    def test_load_or_compute_skips_compute_on_valid_hit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = StageCheckpointStore(Path(directory))
            calls = []

            def compute():
                calls.append("compute")
                return {"version": 1, "segments": []}

            validator = lambda payload: payload.get("version") == 1
            first, first_hit = load_or_compute_json(
                store,
                "translation",
                fingerprint="same",
                artifact_name="translation.checkpoint.json",
                validator=validator,
                compute=compute,
            )
            second, second_hit = load_or_compute_json(
                store,
                "translation",
                fingerprint="same",
                artifact_name="translation.checkpoint.json",
                validator=validator,
                compute=compute,
            )

            self.assertFalse(first_hit)
            self.assertTrue(second_hit)
            self.assertEqual(calls, ["compute"])
            self.assertEqual(first.checksum, second.checksum)


if __name__ == "__main__":
    unittest.main()
