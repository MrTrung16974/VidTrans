"""Local animation worker: VidTrans GPU-worker contract -> ComfyUI LTX-Video.

Implements ``docs/animation-worker-api.md`` (``POST /v1/animate``,
``GET /v1/jobs/{id}``, ``GET /v1/jobs/{id}/video``) on top of a ComfyUI server
using only core nodes and the LTX-Video image-to-video checkpoint. Standard
library only, so it runs with any Python 3.10+ without a virtualenv.

Configuration (environment):

* ``VIDTRANS_COMFYUI_URL``            ComfyUI base URL (default http://127.0.0.1:8188)
* ``VIDTRANS_WORKER_LISTEN``          bind address (default 127.0.0.1)
* ``VIDTRANS_WORKER_PORT``            port (default 9000)
* ``VIDTRANS_ANIMATION_WORKER_TOKEN`` optional bearer token
* ``VIDTRANS_LTXV_CHECKPOINT``        default ltxv-2b-0.9.8-distilled.safetensors
* ``VIDTRANS_LTXV_TEXT_ENCODER``      default t5xxl_fp16.safetensors
* ``VIDTRANS_LTXV_STEPS``             default 8 (distilled model)
* ``VIDTRANS_LTXV_CFG``               default 1.0 (distilled model)
* ``VIDTRANS_LTXV_MAX_SIDE``          longest generated side in px (default 768)
* ``VIDTRANS_LTXV_MAX_FRAMES``        frame cap, 8k+1 (default 97, 4 s at 24 fps)

On an M2 Pro (32 GB) 768 px x 97 frames renders in ~3-4 minutes; 960 px is
four times slower and corrupts on MPS.
* ``VIDTRANS_LTXV_TIMEOUT``           seconds before a job is failed (default 1800)
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

logger = logging.getLogger("vidtrans.animation_worker")

COMFYUI_URL = os.environ.get("VIDTRANS_COMFYUI_URL", "http://127.0.0.1:8188").strip().rstrip("/")
TOKEN = os.environ.get("VIDTRANS_ANIMATION_WORKER_TOKEN", "").strip()
CHECKPOINT = os.environ.get("VIDTRANS_LTXV_CHECKPOINT", "ltxv-2b-0.9.8-distilled.safetensors").strip()
TEXT_ENCODER = os.environ.get("VIDTRANS_LTXV_TEXT_ENCODER", "t5xxl_fp16.safetensors").strip()
STEPS = int(os.environ.get("VIDTRANS_LTXV_STEPS", "8"))
CFG = float(os.environ.get("VIDTRANS_LTXV_CFG", "1.0"))
MAX_SIDE = int(os.environ.get("VIDTRANS_LTXV_MAX_SIDE", "768"))
MAX_FRAMES = int(os.environ.get("VIDTRANS_LTXV_MAX_FRAMES", "97"))
TIMEOUT = float(os.environ.get("VIDTRANS_LTXV_TIMEOUT", "1800"))
MODEL_LABEL = f"comfyui-{CHECKPOINT.rsplit('.', 1)[0]}"
DEFAULT_NEGATIVE = ("worst quality, inconsistent motion, blurry, jittery, distorted face, "
                    "deformed hands, extra fingers, watermark, text")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")


def comfy(path: str, *, body: dict[str, Any] | bytes | None = None, headers: dict[str, str] | None = None,
          raw: bool = False, timeout: float = 30.0):
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    request = urllib.request.Request(
        COMFYUI_URL + path, data=data, method="POST" if data is not None else "GET",
        headers=headers or ({"Content-Type": "application/json"} if isinstance(body, dict) else {}),
    )
    response = urllib.request.urlopen(request, timeout=timeout)
    if raw:
        return response
    with response:
        payload = response.read(16 * 1024 * 1024)
        # /queue and /interrupt answer 200 with an empty body.
        return json.loads(payload.decode("utf-8")) if payload.strip() else {}


def upload_image(png: bytes) -> str:
    name = f"vidtrans-anim-{hashlib.sha256(png).hexdigest()[:20]}.png"
    boundary = f"----VidTrans{uuid.uuid4().hex}"
    body = b"".join([
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode(),
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
         "Content-Type: image/png\r\n\r\n").encode(),
        png,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    uploaded = comfy("/upload/image", body=body, timeout=60.0,
                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    subfolder = str(uploaded.get("subfolder") or "").strip("/\\")
    return f"{subfolder}/{uploaded['name']}" if subfolder else str(uploaded["name"])


def video_geometry(spec: dict[str, Any]) -> tuple[int, int, int, int]:
    """LTX-Video needs sides divisible by 32 and 8k+1 frames."""
    width, height = int(spec.get("width") or 768), int(spec.get("height") or 512)
    scale = min(1.0, MAX_SIDE / max(width, height))
    width = max(256, round(width * scale / 32) * 32)
    height = max(256, round(height * scale / 32) * 32)
    # LTX-Video is trained at 24 fps; VidTrans re-times the clip to the project fps.
    fps = max(8, min(24, int(spec.get("fps") or 24)))
    frames = round(float(spec.get("duration_seconds") or 4.0) * fps)
    frames = max(9, min(MAX_FRAMES, (frames - 1 + 7) // 8 * 8 + 1))
    return width, height, frames, fps


def workflow(spec: dict[str, Any], image_name: str) -> dict[str, Any]:
    width, height, frames, fps = video_geometry(spec)
    seed = int(spec.get("seed") or 0) % (2 ** 32) or int.from_bytes(os.urandom(4), "big")
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CHECKPOINT}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "ltxv"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": str(spec.get("prompt") or ""), "clip": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {
            "text": str(spec.get("negative_prompt") or DEFAULT_NEGATIVE), "clip": ["2", 0]}},
        "5": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "6": {"class_type": "LTXVPreprocess", "inputs": {"image": ["5", 0], "img_compression": 35}},
        "7": {"class_type": "LTXVImgToVideo", "inputs": {
            "positive": ["3", 0], "negative": ["4", 0], "vae": ["1", 2], "image": ["6", 0],
            "width": width, "height": height, "length": frames, "batch_size": 1, "strength": 1.0}},
        "8": {"class_type": "LTXVConditioning", "inputs": {
            "positive": ["7", 0], "negative": ["7", 1], "frame_rate": float(fps)}},
        "9": {"class_type": "LTXVScheduler", "inputs": {
            "steps": STEPS, "max_shift": 2.05, "base_shift": 0.95, "stretch": True,
            "terminal": 0.1, "latent": ["7", 2]}},
        "10": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "11": {"class_type": "SamplerCustom", "inputs": {
            "model": ["1", 0], "add_noise": True, "noise_seed": seed, "cfg": CFG,
            "positive": ["8", 0], "negative": ["8", 1], "sampler": ["10", 0],
            "sigmas": ["9", 0], "latent_image": ["7", 2]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["1", 2]}},
        "13": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "fps": float(fps)}},
        "14": {"class_type": "SaveVideo", "inputs": {
            "video": ["13", 0], "filename_prefix": "VidTrans/anim", "format": "mp4", "codec": "h264"}},
    }


class Jobs:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.by_key: dict[str, str] = {}
        self.pending: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, spec: dict[str, Any]) -> dict[str, Any]:
        key = str(spec.get("idempotency_key") or "")
        with self.lock:
            if key and key in self.by_key:
                return self.jobs[self.by_key[key]]
            job_id = uuid.uuid4().hex
            job = {"job_id": job_id, "status": "queued", "progress": 0.0, "error": None,
                   "model": MODEL_LABEL, "gpu_seconds": 0.0, "_spec": spec, "_video": None}
            self.jobs[job_id] = job
            if key:
                self.by_key[key] = job_id
        self.pending.put(job_id)
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        """Stop a job VidTrans no longer waits for, freeing the GPU for the next render."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job["status"] in {"completed", "failed", "cancelled"}:
                return job
            job["_cancelled"] = True
            # A retry with the same idempotency key must start a new job, not get this one back.
            key = str((job.get("_spec") or {}).get("idempotency_key") or "")
            if key and self.by_key.get(key) == job_id:
                del self.by_key[key]
            if job["status"] == "queued":
                job["status"], job["error"] = "cancelled", "cancelled"
            prompt_id = job.get("_prompt_id")
        if prompt_id:
            try:
                comfy("/queue", body={"delete": [prompt_id]})
                running = comfy("/queue").get("queue_running") or []
                if any(len(entry) > 1 and entry[1] == prompt_id for entry in running):
                    comfy("/interrupt", body={"prompt_id": prompt_id})
            except (OSError, ValueError) as exc:
                logger.warning("Could not cancel ComfyUI prompt %s: %s", prompt_id, exc)
        return job

    def _run(self) -> None:
        # One job at a time: a local GPU cannot run two video models at once.
        while True:
            job = self.jobs[self.pending.get()]
            if job.get("_cancelled"):
                continue
            started = time.monotonic()
            job["status"] = "running"
            try:
                job["_video"] = self._render(job)
                job["status"], job["progress"] = "completed", 1.0
            except Exception as exc:  # reported to VidTrans, which retries or falls back
                if job.get("_cancelled"):
                    job["status"], job["error"] = "cancelled", "cancelled"
                else:
                    logger.exception("Animation job %s failed", job["job_id"])
                    job["status"], job["error"] = "failed", str(exc)[:500]
            finally:
                job["gpu_seconds"] = round(time.monotonic() - started, 1)
                job.pop("_spec", None)

    def _render(self, job: dict[str, Any]) -> dict[str, str]:
        spec = job["_spec"]
        image_name = upload_image(base64.b64decode(spec["image_base64"]))
        queued = comfy("/prompt", body={"prompt": workflow(spec, image_name)})
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI không nhận workflow: {queued.get('error') or queued}")
        job["_prompt_id"] = prompt_id
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            if job.get("_cancelled"):
                raise RuntimeError("cancelled")
            result = comfy(f"/history/{urllib.parse.quote(str(prompt_id))}").get(str(prompt_id)) or {}
            status = result.get("status") or {}
            if status.get("status_str") == "error":
                messages = status.get("messages") or []
                raise RuntimeError(f"ComfyUI lỗi: {str(messages[-1] if messages else status)[:400]}")
            for output in (result.get("outputs") or {}).values():
                for item in output.get("images") or output.get("videos") or []:
                    if str(item.get("filename", "")).lower().endswith(".mp4"):
                        return item
            job["progress"] = min(0.95, (time.monotonic() - deadline + TIMEOUT) / 600)
            time.sleep(2.0)
        raise RuntimeError(f"ComfyUI quá thời gian chờ ({TIMEOUT:.0f} giây)")


JOBS = Jobs()


class Handler(BaseHTTPRequestHandler):
    server_version = "VidTransAnimationWorker/1.0"

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if TOKEN and self.headers.get("Authorization", "") != f"Bearer {TOKEN}":
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/health":
            try:
                comfy("/system_stats", timeout=5.0)
                return self._send(200, {"status": "ok", "model": MODEL_LABEL})
            except (OSError, ValueError) as exc:
                return self._send(503, {"status": "comfyui_unreachable", "error": str(exc)})
        if not self._authorized():
            return
        parts = self.path.strip("/").split("/")
        if len(parts) not in (3, 4) or parts[:2] != ["v1", "jobs"] or not JOB_ID.match(parts[2]):
            return self._send(404, {"error": "not_found"})
        job = JOBS.get(parts[2])
        if not job:
            return self._send(404, {"error": "job_not_found"})
        if len(parts) == 3:
            return self._send(200, self._public(job))
        if parts[3] != "video" or job["status"] != "completed":
            return self._send(404, {"error": "video_not_ready"})
        video = job["_video"]
        query = urllib.parse.urlencode({"filename": video["filename"], "subfolder": video.get("subfolder", ""),
                                        "type": video.get("type", "output")})
        try:
            with comfy(f"/view?{query}", raw=True, timeout=120.0) as response:
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                length = response.headers.get("Content-Length")
                if length:
                    self.send_header("Content-Length", length)
                self.end_headers()
                while block := response.read(1024 * 1024):
                    self.wfile.write(block)
        except (OSError, urllib.error.URLError) as exc:
            self._send(502, {"error": f"comfyui_view_failed: {exc}"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        if not self._authorized():
            return
        parts = self.path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["v1", "jobs"] and parts[3] == "cancel" and JOB_ID.match(parts[2]):
            job = JOBS.cancel(parts[2])
            if not job:
                return self._send(404, {"error": "job_not_found"})
            return self._send(200, self._public(job))
        if self.path != "/v1/animate":
            return self._send(404, {"error": "not_found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            spec = json.loads(self.rfile.read(min(length, 64 * 1024 * 1024)))
            base64.b64decode(spec["image_base64"], validate=True)
        except (ValueError, KeyError, TypeError):
            return self._send(400, {"error": "invalid_request"})
        job = JOBS.submit(spec)
        self._send(200, {"job_id": job["job_id"], "status": job["status"]})

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    listen = os.environ.get("VIDTRANS_WORKER_LISTEN", "127.0.0.1")
    port = int(os.environ.get("VIDTRANS_WORKER_PORT", "9000"))
    logger.info("Animation worker on http://%s:%s -> %s (%s)", listen, port, COMFYUI_URL, CHECKPOINT)
    ThreadingHTTPServer((listen, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
