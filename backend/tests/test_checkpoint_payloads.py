from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from pipeline.checkpoint_payloads import (
    restore_file_checkpoint,
    restore_tts_checkpoint,
    serialize_tts_checkpoint,
    valid_file_checkpoint,
    valid_transcript_checkpoint,
    valid_translation_checkpoint,
    valid_tts_checkpoint,
)


class CheckpointPayloadTests(unittest.TestCase):
    def test_accepts_valid_transcript_payload(self) -> None:
        self.assertTrue(
            valid_transcript_checkpoint(
                {
                    "version": 1,
                    "source_method": "speech",
                    "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
                }
            )
        )

    def test_rejects_transcript_with_invalid_source_method(self) -> None:
        self.assertFalse(
            valid_transcript_checkpoint(
                {
                    "version": 1,
                    "source_method": "unknown",
                    "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
                }
            )
        )

    def test_translation_requires_complete_active_segment(self) -> None:
        complete = {
            "version": 1,
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "Xin chào", "translation_status": "translated"}
            ],
        }
        failed = {
            "version": 1,
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "你好", "translation_status": "source_fallback"}
            ],
        }
        skipped_only = {
            "version": 1,
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "", "skip_subtitle": True, "translation_status": "skipped"}
            ],
        }
        self.assertTrue(valid_translation_checkpoint(complete))
        self.assertFalse(valid_translation_checkpoint(failed))
        self.assertFalse(valid_translation_checkpoint(skipped_only))

    def test_tts_checkpoint_round_trip_restores_only_tts_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tts_path = root / "tts" / "0000_fitted.wav"
            tts_path.parent.mkdir()
            tts_path.write_bytes(b"cue")
            voice_track = root / "voice_track.wav"
            voice_track.write_bytes(b"track")
            source_segments = [
                {
                    "text": "Xin chào",
                    "subtitle_layout": {"lines": ["old"]},
                    "voice_type": "female",
                    "tts_duration": 0.8,
                    "tts_start": 0.1,
                    "tts_end": 0.9,
                    "tts_path": tts_path,
                }
            ]

            payload, dependencies = serialize_tts_checkpoint(
                source_segments, work_dir=root, voice_track=voice_track
            )
            target_segments = [{"text": "Xin chào", "subtitle_layout": {"lines": ["new"]}}]
            restored_voice_track = restore_tts_checkpoint(
                target_segments, payload, work_dir=root
            )

            self.assertTrue(valid_tts_checkpoint(payload))
            self.assertEqual(dependencies, ["tts/0000_fitted.wav", "voice_track.wav"])
            self.assertEqual(target_segments[0]["tts_path"], tts_path.resolve())
            self.assertEqual(target_segments[0]["subtitle_layout"], {"lines": ["new"]})
            self.assertEqual(restored_voice_track, voice_track.resolve())

    def test_tts_checkpoint_rejects_segment_count_mismatch(self) -> None:
        payload = {"version": 1, "segment_count": 0, "states": [], "voice_track": "voice.wav"}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "does not match"):
                restore_tts_checkpoint([{"text": "cue"}], payload, work_dir=Path(directory))

    def test_file_checkpoint_schema_and_safe_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {"version": 1, "artifact": "mixed_audio.wav"}
            self.assertTrue(valid_file_checkpoint(payload))
            self.assertEqual(
                restore_file_checkpoint(payload, work_dir=root),
                (root / "mixed_audio.wav").resolve(),
            )
            with self.assertRaisesRegex(ValueError, "escapes"):
                restore_file_checkpoint(
                    {"version": 1, "artifact": "../outside.wav"}, work_dir=root
                )


if __name__ == "__main__":
    unittest.main()
