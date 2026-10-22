"""Image-to-video generators behind one interface.

* ``camera_2_5d`` - local FFmpeg camera move over the keyframe (no GPU, always
  available). It is also the fallback when an AI provider fails or the
  render's AI budget is spent.
* ``veo`` - Google Veo through the Gemini API long-running ``predictLongRunning``.
* ``gpu_worker`` - a self-hosted GPU server (Wan, HunyuanVideo, ComfyUI...)
  that implements the small HTTP contract in ``docs/animation-worker-api.md``.

Providers return the raw clip; the producer validates, normalises (size, fps,
duration) and caches it. A process-wide semaphore caps concurrent AI video
generations, independent of the general job worker concurrency.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_VEO_MODEL = "veo-3.0-fast-generate-001"


class VideoGenerationError(RuntimeError):
    """A failed attempt; the producer may retry with another seed or fall back."""

    retryable = True


class VideoConfigurationError(VideoGenerationError):
    retryable = False


@dataclass(frozen=True)
class VideoRequest:
    keyframe: Path
    prompt: str
    duration_seconds: float
    width: int
    height: int
    fps: int
    aspect_ratio: str
    negative_prompt: str = ""
    seed: int = 0
    motion_strength: float = 0.45
    camera_movement: str = "static"
    idempotency_key: str = ""


@dataclass
class VideoResult:
    model: str
    billed_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class VideoProvider(Protocol):
    key: str
    is_ai: bool

    @property
    def cache_key(self) -> str: ...

    def generation_duration(self, requested: float) -> float: ...

    def generate(self, request: VideoRequest, output: Path, check_active: Callable[[], None]) -> VideoResult: ...


_slots_lock = threading.Lock()
_slots: threading.BoundedSemaphore | None = None


def _animation_slots() -> threading.BoundedSemaphore:
    global _slots
    with _slots_lock:
        if _slots is None:
            try:
                count = max(1, int(os.environ.get("VIDTRANS_ANIMATION_CONCURRENCY", "1")))
            except ValueError:
                count = 1
            _slots = threading.BoundedSemaphore(count)
        return _slots


@contextmanager
def animation_slot(check_active: Callable[[], None], on_wait: Callable[[], None] = lambda: None) -> Iterator[None]:
    """Hold one of the AI-video slots; waiting stays cancellable."""
    slots = _animation_slots()
    waited = False
    while not slots.acquire(timeout=1.0):
        if not waited:
            on_wait()
            waited = True
        check_active()
    try:
        yield
    finally:
        slots.release()


# ---------------------------------------------------------------- 2.5D camera
def camera_expressions(movement: str, frames: int, strength: float) -> tuple[str, str, str]:
    """zoompan z/x/y for a camera move; ``strength`` scales how far the camera travels."""
    p = f"(on/{max(1, frames - 1)})"
    amount = 0.06 + 0.16 * max(0.0, min(1.0, strength))
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    base = f"{1 + amount:.4f}"
    if movement == "push_in":
        return f"1+{amount:.4f}*{p}", cx, cy
    if movement == "pull_out":
        return f"{1 + amount:.4f}-{amount:.4f}*{p}", cx, cy
    if movement == "pan_left":
        return base, f"(iw-iw/zoom)*(1-{p})", cy
    if movement == "pan_right":
        return base, f"(iw-iw/zoom)*{p}", cy
    if movement == "tilt_up":
        return base, cx, f"(ih-ih/zoom)*(1-{p})"
    if movement == "tilt_down":
        return base, cx, f"(ih-ih/zoom)*{p}"
    if movement == "tracking":
        return f"{1 + amount * 0.6:.4f}+{amount * 0.4:.4f}*{p}", f"(iw-iw/zoom)*{p}", cy
    if movement == "handheld":
        sway = f"{amount * 0.25:.4f}"
        return (base, f"(iw-iw/zoom)/2+(iw-iw/zoom)/2*{sway}*8*sin(on/11)",
                f"(ih-ih/zoom)/2+(ih-ih/zoom)/2*{sway}*8*sin(on/17)")
    # Static: a slow "breathing" push keeps a still keyframe from looking frozen.
    return f"1+{amount * 0.25:.4f}*{p}", cx, cy


@dataclass
class Camera25DProvider:
    ffmpeg: str
    run_ffmpeg: Callable[[list[str]], Any]
    key = "camera_2_5d"
    is_ai = False
    cache_key = "camera_2_5d-v1"

    def generation_duration(self, requested: float) -> float:
        return requested

    def generate(self, request: VideoRequest, output: Path, check_active: Callable[[], None]) -> VideoResult:
        frames = max(1, round(request.duration_seconds * request.fps))
        zoom, x, y = camera_expressions(request.camera_movement, frames, request.motion_strength)
        w, h = request.width, request.height
        graph = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
                 f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={request.fps},setsar=1,format=yuv420p")
        output.parent.mkdir(parents=True, exist_ok=True)
        self.run_ffmpeg([self.ffmpeg, "-y", "-i", str(request.keyframe), "-vf", graph, "-frames:v", str(frames),
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an",
                         str(output)])
        return VideoResult(model=self.cache_key)


# ------------------------------------------------------------------------ Veo
@dataclass
class VeoProvider:
    key = "veo"
    is_ai = True
    timeout: float = 120.0
    poll_seconds: float = 10.0
    max_wait_seconds: float = 900.0
    opener: Callable[..., Any] | None = None
    sleeper: Callable[[float], None] = time.sleep
    model: str = field(default_factory=lambda: os.environ.get("VIDTRANS_VEO_MODEL", DEFAULT_VEO_MODEL).strip()
                       or DEFAULT_VEO_MODEL)

    def __post_init__(self) -> None:
        self.api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not self.api_key:
            raise VideoConfigurationError("Veo cần GEMINI_API_KEY")
        self._open = self.opener or urllib.request.urlopen
        raw = os.environ.get("VIDTRANS_VEO_DURATIONS", "4,6,8")
        self.durations = sorted({float(v) for v in raw.split(",") if v.strip()}) or [8.0]

    @property
    def cache_key(self) -> str:
        return f"veo-{self.model}"

    def generation_duration(self, requested: float) -> float:
        """Veo only renders fixed lengths: the shortest one that covers the shot (else the longest)."""
        return next((d for d in self.durations if d >= requested - 0.25), self.durations[-1])

    def _request(self, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method="POST" if data else "GET", headers={
            "Content-Type": "application/json", "x-goog-api-key": self.api_key})
        try:
            with self._open(request, timeout=self.timeout) as response:
                return json.loads(response.read(16 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 401, 403, 404}:
                error = VideoConfigurationError(f"Veo từ chối yêu cầu (HTTP {exc.code})")
                error.retryable = exc.code == 400  # A rejected prompt may pass with another seed.
                raise error from exc
            raise VideoGenerationError(f"Veo lỗi HTTP {exc.code}") from exc
        except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
            raise VideoGenerationError("Không kết nối được Veo") from exc

    def generate(self, request: VideoRequest, output: Path, check_active: Callable[[], None]) -> VideoResult:
        duration = self.generation_duration(request.duration_seconds)
        parameters: dict[str, Any] = {
            "aspectRatio": request.aspect_ratio, "durationSeconds": int(duration),
            "personGeneration": "allow_adult",
        }
        if request.negative_prompt:
            parameters["negativePrompt"] = request.negative_prompt
        if request.seed:
            parameters["seed"] = request.seed
        image = base64.b64encode(Path(request.keyframe).read_bytes()).decode("ascii")
        operation = self._request(f"{GEMINI_BASE_URL}/models/{self.model}:predictLongRunning", {
            "instances": [{"prompt": request.prompt, "image": {"bytesBase64Encoded": image, "mimeType": "image/png"}}],
            "parameters": parameters,
        })
        name = operation.get("name")
        if not name:
            raise VideoGenerationError("Veo không trả về mã tác vụ")
        waited = 0.0
        while not operation.get("done"):
            check_active()
            if waited >= self.max_wait_seconds:
                raise VideoGenerationError(f"Veo chưa xong sau {self.max_wait_seconds:.0f} giây")
            self.sleeper(self.poll_seconds)
            waited += self.poll_seconds
            operation = self._request(f"{GEMINI_BASE_URL}/{name}")
        if operation.get("error"):
            raise VideoGenerationError(f"Veo lỗi: {str(operation['error'].get('message', ''))[:200]}")
        samples = (((operation.get("response") or {}).get("generateVideoResponse") or {}).get("generatedSamples")
                   or [])
        uri = ((samples[0] if samples else {}).get("video") or {}).get("uri")
        if not uri:
            raise VideoGenerationError("Veo không trả về video (có thể bị bộ lọc an toàn chặn)")
        download = urllib.request.Request(uri, headers={"x-goog-api-key": self.api_key})
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._open(download, timeout=self.timeout) as response, output.open("wb") as handle:
                while block := response.read(1024 * 1024):
                    handle.write(block)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            output.unlink(missing_ok=True)
            raise VideoGenerationError("Không tải được video Veo") from exc
        return VideoResult(model=self.cache_key, billed_seconds=duration, metadata={"operation": name})


# ----------------------------------------------------------------- GPU worker
@dataclass
class GPUWorkerProvider:
    key = "gpu_worker"
    is_ai = True
    timeout: float = 60.0
    poll_seconds: float = 3.0
    max_wait_seconds: float = 1800.0
    opener: Callable[..., Any] | None = None
    sleeper: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self.url = os.environ.get("VIDTRANS_ANIMATION_WORKER_URL", "").strip().rstrip("/")
        if not self.url:
            raise VideoConfigurationError("Chưa cấu hình VIDTRANS_ANIMATION_WORKER_URL cho GPU worker")
        self.token = os.environ.get("VIDTRANS_ANIMATION_WORKER_TOKEN", "").strip()
        self.model = os.environ.get("VIDTRANS_ANIMATION_WORKER_MODEL", "gpu-worker").strip() or "gpu-worker"
        self.max_duration = float(os.environ.get("VIDTRANS_ANIMATION_WORKER_MAX_SECONDS", "8") or 8)
        self._open = self.opener or urllib.request.urlopen

    @property
    def cache_key(self) -> str:
        return f"gpu_worker-{self.model}"

    def generation_duration(self, requested: float) -> float:
        return min(requested, self.max_duration)

    def _call(self, path: str, body: dict[str, Any] | None = None, *, raw: bool = False):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(f"{self.url}{path}", data=data, headers=headers,
                                         method="POST" if data else "GET")
        try:
            response = self._open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403, 404}:
                raise VideoConfigurationError(f"GPU worker từ chối yêu cầu (HTTP {exc.code})") from exc
            raise VideoGenerationError(f"GPU worker lỗi HTTP {exc.code}") from exc
        except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
            raise VideoGenerationError("Không kết nối được GPU worker") from exc
        if raw:
            return response
        with response:
            return json.loads(response.read(1024 * 1024).decode("utf-8"))

    def generate(self, request: VideoRequest, output: Path, check_active: Callable[[], None]) -> VideoResult:
        duration = self.generation_duration(request.duration_seconds)
        job = self._call("/v1/animate", {
            "image_base64": base64.b64encode(Path(request.keyframe).read_bytes()).decode("ascii"),
            "prompt": request.prompt, "negative_prompt": request.negative_prompt,
            "duration_seconds": duration, "fps": request.fps, "width": request.width, "height": request.height,
            "seed": request.seed, "motion_strength": request.motion_strength,
            "idempotency_key": request.idempotency_key,
        })
        job_id = str(job.get("job_id") or "")
        if not job_id:
            raise VideoGenerationError("GPU worker không trả về job_id")
        status: dict[str, Any] = job
        waited = 0.0
        while status.get("status") not in {"completed", "failed"}:
            check_active()
            if waited >= self.max_wait_seconds:
                raise VideoGenerationError("GPU worker quá thời gian chờ")
            self.sleeper(self.poll_seconds)
            waited += self.poll_seconds
            status = self._call(f"/v1/jobs/{job_id}")
        if status["status"] == "failed":
            raise VideoGenerationError(f"GPU worker lỗi: {str(status.get('error') or 'không rõ')[:200]}")
        output.parent.mkdir(parents=True, exist_ok=True)
        with self._call(f"/v1/jobs/{job_id}/video", raw=True) as response, output.open("wb") as handle:
            while block := response.read(1024 * 1024):
                handle.write(block)
        return VideoResult(model=str(status.get("model") or self.cache_key),
                           billed_seconds=float(status.get("gpu_seconds") or duration),
                           metadata={"job_id": job_id})


def animation_provider_statuses() -> list[dict[str, Any]]:
    return [
        {"key": "camera_2_5d", "label": "Camera 2.5D (không cần GPU, miễn phí)", "configured": True, "ai": False},
        {"key": "veo", "label": "Google Veo (AI, qua Gemini API, tính phí)",
         "configured": bool(os.environ.get("GEMINI_API_KEY", "").strip()), "ai": True},
        {"key": "gpu_worker", "label": "GPU worker riêng (Wan / HunyuanVideo / ComfyUI)",
         "configured": bool(os.environ.get("VIDTRANS_ANIMATION_WORKER_URL", "").strip()), "ai": True},
    ]


def create_video_provider(key: str, *, ffmpeg: str, run_ffmpeg: Callable[[list[str]], Any]) -> VideoProvider:
    if key == "camera_2_5d":
        return Camera25DProvider(ffmpeg, run_ffmpeg)
    if key == "veo":
        return VeoProvider()
    if key == "gpu_worker":
        return GPUWorkerProvider()
    raise ValueError(f"animation_provider không hợp lệ: {key}")
