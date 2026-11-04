from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import main


class CompletedJobCheckpointCleanupTests(unittest.TestCase):
    def _job_dir(self, work: Path, job_id: str, *, age_hours: float) -> Path:
        checkpoints = work / "users" / "owner" / job_id / "checkpoints"
        for stage in ("render", "audio_mix", "tts", "transcript", "translation"):
            (checkpoints / stage).mkdir(parents=True)
            (checkpoints / stage / "artifact.bin").write_bytes(b"x" * 100)
        stamp = datetime.now(timezone.utc).timestamp() - age_hours * 3600
        for stage in ("render", "audio_mix"):
            os.utime(checkpoints / stage, (stamp, stamp))
        return checkpoints

    def test_only_render_and_mix_of_old_completed_jobs_are_removed(self) -> None:
        statuses = {"done": "completed", "fresh": "completed", "failed": "failed", "running": "processing"}
        service = Mock()
        service.get.side_effect = lambda job_id: {"status": statuses[job_id]}
        with TemporaryDirectory() as directory:
            work = Path(directory)
            dirs = {
                "done": self._job_dir(work, "done", age_hours=12),
                "fresh": self._job_dir(work, "fresh", age_hours=1),
                "failed": self._job_dir(work, "failed", age_hours=12),
                "running": self._job_dir(work, "running", age_hours=12),
            }
            with patch.object(main, "WORK_DIR", work), patch.object(main, "JOB_SERVICE", service), \
                    patch.dict(os.environ, {"VIDTRANS_CHECKPOINT_RETENTION_HOURS": "6"}):
                removed, freed = main.prune_completed_job_checkpoints(datetime.now(timezone.utc))

            self.assertEqual((removed, freed), (2, 200))
            self.assertFalse((dirs["done"] / "render").exists())
            self.assertFalse((dirs["done"] / "audio_mix").exists())
            for stage in ("tts", "transcript", "translation"):
                self.assertTrue((dirs["done"] / stage / "artifact.bin").is_file())
            for job_id in ("fresh", "failed", "running"):
                self.assertTrue((dirs[job_id] / "render").is_dir(), job_id)


if __name__ == "__main__":
    unittest.main()
