"""Isolated crash/restart SIT for TTS, audio and render checkpoints.

The harness copies backend source to a temporary directory, uses a temporary
SQLite/work/output tree, seeds deterministic transcript/translation checkpoints,
kills the first worker after TTS commit, then resumes the persisted job.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
JOB_ID = "sit-kill-restart"


WORKER_SOURCE = r'''
from __future__ import annotations

import json
import sys
from pathlib import Path

import main
from infrastructure.media_probe import probe_media_file
from infrastructure.stage_checkpoint import StageCheckpointStore, fingerprint_payload, sha256_file
from pipeline.translation import segment_filter_signature


JOB_ID = "sit-kill-restart"


def request_payload(video_path: Path) -> dict:
    return {
        "video_path": str(video_path),
        "mode": 2,
        "whisper_model": "tiny",
        "voice_type": "female",
        "voice_mode": "manual",
        "voice_style": "natural",
        "speech_rate": 1.0,
        "background_music_path": None,
        "keep_original_audio": False,
        "original_audio_volume": 0.2,
        "music_volume": 0.15,
        "subtitle_style": None,
        "subtitle_source": "whisper",
        "ocr_sample_fps": 2.0,
        "ocr_roi_top": 0.55,
        "ocr_roi_bottom": 1.0,
        "source_language": "zh-CN",
        "target_language": "vi",
        "generate_tiktok_post": False,
        "tiktok_max_summary_chars": 350,
        "tiktok_hashtag_count": 6,
        "auto_publish_tiktok": False,
        "tiktok_privacy_level": "SELF_ONLY",
        "tiktok_publish_at": None,
    }


def prepare() -> None:
    video_path = main.UPLOAD_DIR / "source.mp4"
    request = request_payload(video_path)
    main.JOB_SERVICE.create(
        JOB_ID,
        {
            "status": "processing",
            "step": "tts",
            "progress": 0.5,
            "filename": video_path.name,
            "cancel_requested": 0,
            "resume_request": request,
        },
    )
    probe = probe_media_file(video_path, ffprobe=main.FFPROBE)
    source_checksum = sha256_file(video_path)
    store = StageCheckpointStore(main.WORK_DIR / JOB_ID)
    transcript_fingerprint = fingerprint_payload(
        {
            "version": 2,
            "source_checksum": source_checksum,
            "subtitle_source": "whisper",
            "source_language": "zh-CN",
            "whisper_model": "tiny",
            "asr_engine": main.ASR_SERVICE.engine_name,
            "asr_compute_type": main.ASR_SERVICE.config.compute_type,
            "asr_beam_size": main.ASR_SERVICE.config.beam_size,
            "asr_vad": main.ASR_SERVICE.config.vad,
            "asr_vad_min_silence_ms": main.ASR_SERVICE.config.vad_min_silence_ms,
            "ocr_enabled": main.SETTINGS.ocr_backend != "off",
            "ocr_backend": main.SETTINGS.ocr_backend,
            "ocr_sample_fps": 2.0,
            "ocr_roi_top": 0.55,
            "ocr_roi_bottom": 1.0,
            "segmentation": {
                "preferred_chars": 14,
                "max_chars": 20,
                "pause_seconds": 0.65,
                "max_duration_seconds": 3.2,
            },
            "filter": segment_filter_signature(),
        }
    )
    transcript = store.commit_json(
        "transcript",
        fingerprint=transcript_fingerprint,
        artifact_name=f"checkpoints/transcript/{transcript_fingerprint}.json",
        payload={
            "version": 1,
            "source_method": "speech",
            "detected_language": "zh-CN",
            "detected_language_probability": 1.0,
            "segments": [
                {
                    "start": 0.0,
                    "end": 3.8,
                    "text": "你好，欢迎来到视频测试。",
                    "skip_subtitle": False,
                    "skip_tts": False,
                }
            ],
        },
    )
    translation_fingerprint = fingerprint_payload(
        {
            "version": 1,
            "transcript_checksum": transcript.checksum,
            "source_language": "zh-CN",
            "target_language": "vi",
            "translation_pipeline_version": 2,
            "max_batch_chars": 1200,
        }
    )
    store.commit_json(
        "translation",
        fingerprint=translation_fingerprint,
        artifact_name=f"checkpoints/translation/{translation_fingerprint}.json",
        payload={
            "version": 1,
            "segments": [
                {
                    "start": 0.0,
                    "end": 3.8,
                    "source_text": "你好，欢迎来到视频测试。",
                    "text": "Xin chào và chào mừng bạn đến với bài kiểm tra video.",
                    "translation_status": "translated",
                    "skip_subtitle": False,
                    "skip_tts": False,
                }
            ],
        },
    )
    print(json.dumps({"prepared": True, "duration": probe.duration}))


def run_first() -> None:
    job = main.JOB_SERVICE.get(JOB_ID)
    main.resume_process_video(JOB_ID, dict(job["resume_request"]))


def restart() -> None:
    main.JOB_SERVICE.requeue_interrupted()
    claimed = main.JOB_SERVICE.claim_next("sit-restart")
    if claimed is None:
        raise RuntimeError("restart did not claim the interrupted job")
    job_id, job = claimed
    main.resume_process_video(job_id, dict(job["resume_request"]))
    print(json.dumps(main.JOB_SERVICE.get(job_id), ensure_ascii=False))


def status() -> None:
    print(json.dumps(main.JOB_SERVICE.get(JOB_ID), ensure_ascii=False))


actions = {"prepare": prepare, "run": run_first, "restart": restart, "status": status}
actions[sys.argv[1]]()
'''


def run(command: list[str], *, cwd: Path, timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(cwd)
    environment["VIDTRANS_OCR_BACKEND"] = "off"
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def main() -> None:
    sandbox = Path(tempfile.mkdtemp(prefix="vidtrans-kill-restart-"))
    backend = sandbox / "backend"
    shutil.copytree(
        BACKEND_ROOT,
        backend,
        ignore=shutil.ignore_patterns("work", "outputs", "uploads", "__pycache__", "*.pyc"),
    )
    worker = backend / "sit_worker.py"
    worker.write_text(WORKER_SOURCE, encoding="utf-8")
    uploads = backend / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    source_video = uploads / "source.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24:duration=12",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=12",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source_video),
        ],
        cwd=backend,
    )
    run([sys.executable, "-B", str(worker), "prepare"], cwd=backend)

    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(backend)
    environment["VIDTRANS_OCR_BACKEND"] = "off"
    first = subprocess.Popen(
        [sys.executable, "-B", str(worker), "run"],
        cwd=backend,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stages_at_kill: list[str] = []
    tts_fingerprint_at_kill: str | None = None
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if first.poll() is not None:
            stdout, stderr = first.communicate()
            raise RuntimeError(
                "first worker exited before crash injection\n"
                + stdout[-2000:]
                + "\n"
                + stderr[-4000:]
            )
        try:
            matches = list((backend / "work").glob(f"users/*/{JOB_ID}/stage_manifest.json"))
            manifest_path = matches[0] if matches else backend / "work" / JOB_ID / "stage_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            stages_at_kill = sorted(manifest.get("stages", {}))
        except (OSError, json.JSONDecodeError):
            stages_at_kill = []
        if "tts" in stages_at_kill:
            # The manifest is atomic, while large WAV dependency writes may
            # still be reaching the filesystem. Confirm the committed bundle
            # remains readable before injecting SIGKILL.
            time.sleep(0.25)
            try:
                stable_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                tts_record = stable_manifest.get("stages", {}).get("tts", {})
                tts_fingerprint_at_kill = tts_record.get("fingerprint")
                dependencies = tts_record.get("dependencies", [])
                dependency_paths = [manifest_path.parent / item["path"] for item in dependencies]
                checksums_match = bool(dependencies)
                for item, path in zip(dependencies, dependency_paths):
                    if not path.is_file() or not path.stat().st_size:
                        checksums_match = False
                        break
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    if digest != item.get("checksum"):
                        checksums_match = False
                        break
                if not checksums_match:
                    continue
                stages_at_kill = sorted(stable_manifest.get("stages", {}))
            except (OSError, KeyError, json.JSONDecodeError):
                continue
            first.kill()
            first.wait(timeout=10)
            break
        time.sleep(0.02)
    else:
        first.kill()
        first.wait(timeout=10)
        raise TimeoutError("TTS checkpoint was not committed before timeout")

    restarted = run([sys.executable, "-B", str(worker), "restart"], cwd=backend, timeout=240)
    final_job = json.loads(restarted.stdout.strip().splitlines()[-1])
    output_matches = list((backend / "outputs").glob(f"users/*/{JOB_ID}/output_{JOB_ID}.mp4"))
    output_video = output_matches[0] if output_matches else backend / "outputs" / f"output_{JOB_ID}.mp4"
    probe = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(output_video),
        ],
        cwd=backend,
    )
    metadata = json.loads(probe.stdout)
    output_duration = float(metadata.get("format", {}).get("duration") or 0.0)
    source_duration = 12.0
    duration_delta = round(abs(source_duration - output_duration), 3)
    stage_cache = final_job.get("stage_cache", {})
    if final_job.get("status") != "completed":
        raise RuntimeError(f"restarted job did not complete: {final_job.get('status')}")
    if stage_cache.get("transcript") != "hit" or stage_cache.get("translation") != "hit":
        raise RuntimeError(f"upstream checkpoints were not reused: {stage_cache}")
    if stage_cache.get("tts") != "hit":
        final_manifests = list((backend / "work").glob(f"users/*/{JOB_ID}/stage_manifest.json"))
        final_tts_fingerprint = None
        if final_manifests:
            final_payload = json.loads(final_manifests[0].read_text(encoding="utf-8"))
            final_tts_fingerprint = final_payload.get("stages", {}).get("tts", {}).get("fingerprint")
        raise RuntimeError(
            "TTS checkpoint was not reused after restart: "
            f"{stage_cache}; before={tts_fingerprint_at_kill}; after={final_tts_fingerprint}"
        )
    stream_types = {stream.get("codec_type") for stream in metadata.get("streams", [])}
    if stream_types != {"video", "audio"}:
        raise RuntimeError(f"unexpected output streams: {sorted(stream_types)}")

    print(
        json.dumps(
            {
                "status": (
                    "PASS" if duration_delta <= 0.25 else "PASS_RECOVERY_WITH_MEDIA_DEFECT"
                ),
                "crash_return_code": first.returncode,
                "stages_at_kill": stages_at_kill,
                "restart_count": final_job.get("restart_count"),
                "stage_cache_after_restart": stage_cache,
                "probe": metadata,
                "source_duration": source_duration,
                "output_duration": output_duration,
                "duration_delta": duration_delta,
                "media_duration_within_tolerance": duration_delta <= 0.25,
                "sandbox": str(sandbox),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
