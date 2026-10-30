"""Scene image generators behind one interface.

GPU or API key; ``comfyui`` uses a locally hosted ComfyUI server; ``cloudflare``
(Workers AI free tier) and ``pollinations`` (no account) are free hosted APIs; ``gemini``
calls the Gemini image model and accepts character reference images for
consistency. Every provider writes a PNG of exactly the requested size, so the
renderer never has to care which one produced it.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import textwrap
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
CLOUDFLARE_BASE_URL = "https://api.cloudflare.com/client/v4/accounts"
DEFAULT_CLOUDFLARE_IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt"
# Anonymous calls get one image per 45 s per IP; anything faster is refused with
# 402 FREE_LIMIT, so parallel scenes all fell back to stand-ins.
POLLINATIONS_FREE_INTERVAL_SECONDS = 46.0
# The API rejects a larger seed with 400 "Query parameter validation failed".
POLLINATIONS_MAX_SEED = 2**31 - 1
_POLLINATIONS_LOCK = threading.Lock()
_pollinations_last_call = [float("-inf")]
_RETRY_AFTER_RE = re.compile(r"retry after (\d+)\s*s", re.IGNORECASE)
logger = logging.getLogger(__name__)


class ImageGenerationError(RuntimeError):
    rate_limited = False
    # The provider is out of quota or down: later images in the same render would fail too.
    unavailable = False


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    width: int
    height: int
    negative_prompt: str = ""
    seed: int = 0
    references: tuple[Path, ...] = ()
    # An approved picture of the place: keep its architecture, materials and colours, not its framing.
    setting_reference: Path | None = None
    caption: str = ""
    aspect_ratio: str = "9:16"
    # What a stand-in image shows; generative providers ignore it and use ``prompt``.
    display_text: str = ""


class ImageProvider(Protocol):
    key: str
    supports_references: bool

    @property
    def cache_key(self) -> str: ...

    def generate(self, request: ImageRequest, output_path: Path) -> None: ...


def fit_cover(image, width: int, height: int):
    from PIL import Image

    image = image.convert("RGB")
    scale = max(width / image.width, height / image.height)
    resized = image.resize((max(width, round(image.width * scale)), max(height, round(image.height * scale))),
                           Image.Resampling.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def save_png_atomic(image, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".tmp.png")
    image.save(temporary, format="PNG", optimize=True)
    temporary.replace(output_path)


_PALETTES = [
    ((26, 32, 64), (110, 72, 140)), ((18, 52, 59), (64, 145, 108)), ((60, 22, 32), (196, 92, 64)),
    ((20, 28, 38), (70, 98, 140)), ((48, 34, 20), (196, 150, 82)), ((30, 18, 48), (180, 84, 140)),
]


class PlaceholderImageProvider:
    """Offline stand-in: a gradient panel that shows what the scene will depict."""

    key = "placeholder"
    supports_references = False
    cache_key = "placeholder-v2"

    def __init__(self, font_path: Path | None = None) -> None:
        self.font_path = font_path

    def _font(self, size: int):
        from PIL import ImageFont

        if self.font_path and Path(self.font_path).is_file():
            return ImageFont.truetype(str(self.font_path), size)
        return ImageFont.load_default(size=size)

    def generate(self, request: ImageRequest, output_path: Path) -> None:
        from PIL import Image, ImageDraw

        width, height = request.width, request.height
        digest = hashlib.sha256(request.prompt.encode("utf-8")).digest()
        top, bottom = _PALETTES[digest[0] % len(_PALETTES)]
        gradient = Image.new("RGB", (1, 256))
        for y in range(256):
            t = y / 255
            gradient.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
        image = gradient.resize((width, height))
        draw = ImageDraw.Draw(image)
        unit = min(width, height)
        # Deterministic soft shapes so consecutive scenes are visually distinct.
        for index in range(5):
            radius = unit * (0.12 + digest[index + 1] / 255 * 0.25)
            cx = digest[index + 6] / 255 * width
            cy = digest[index + 11] / 255 * height * 0.7
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                         outline=(255, 255, 255), width=max(2, unit // 300))
        title_font = self._font(max(24, unit // 14))
        body_font = self._font(max(18, unit // 30))
        margin = unit // 12
        y = margin
        if request.caption:
            draw.text((margin, y), request.caption, font=title_font, fill=(255, 255, 255))
            y += int(unit / 14 * 1.6)
        chars_per_line = max(16, int((width - 2 * margin) / (unit / 30 * 0.55)))
        for line in textwrap.wrap(request.display_text or request.prompt, chars_per_line)[:12]:
            draw.text((margin, y), line, font=body_font, fill=(235, 238, 245))
            y += int(unit / 30 * 1.45)
        draw.text((margin, height - margin * 2.2), "Ảnh minh họa tạm — chọn AI vẽ ảnh để sinh ảnh thật",
                  font=body_font, fill=(255, 255, 255))
        save_png_atomic(image, output_path)


@dataclass
class ComfyUIImageProvider:
    """Generate images through ComfyUI's built-in HTTP API.

    The base workflow uses core nodes. When reference images are supplied it
    applies the first character one through the pinned official IP-Adapter custom node,
    and the place's picture through a second, weaker IP-Adapter.
    A plain-checkpoint retry keeps renders working if IP-Adapter is unavailable
    or the local GPU cannot fit it in VRAM.
    """

    key = "comfyui"
    supports_references = True
    timeout: float = 300.0
    poll_interval: float = 1.0
    # A dropped connection between two prompts (ComfyUI busy freeing models) must not mark the
    # provider down: the render would then draw a stand-in for every remaining shot.
    connect_attempts: int = 4
    # Set by the render: raises when the job is cancelled, so a stop request does not wait
    # for the image ComfyUI is drawing (and that prompt is removed from ComfyUI's GPU).
    check_active: Callable[[], None] | None = None
    opener: Callable[..., Any] | None = None
    sleeper: Callable[[float], None] = time.sleep
    base_url: str = field(default_factory=lambda: os.environ.get("VIDTRANS_COMFYUI_URL", "").strip().rstrip("/"))
    checkpoint: str = field(default_factory=lambda: os.environ.get("VIDTRANS_COMFYUI_CHECKPOINT", "").strip())
    steps: int = field(default_factory=lambda: int(os.environ.get("VIDTRANS_COMFYUI_STEPS", "20")))
    cfg: float = field(default_factory=lambda: float(os.environ.get("VIDTRANS_COMFYUI_CFG", "7")))
    sampler: str = field(default_factory=lambda: os.environ.get("VIDTRANS_COMFYUI_SAMPLER", "euler").strip())
    scheduler: str = field(default_factory=lambda: os.environ.get("VIDTRANS_COMFYUI_SCHEDULER", "normal").strip())
    max_side: int = field(default_factory=lambda: int(os.environ.get("VIDTRANS_COMFYUI_MAX_SIDE", "1024")))
    ipadapter_enabled: bool = field(default_factory=lambda: os.environ.get(
        "VIDTRANS_COMFYUI_IPADAPTER_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"})
    ipadapter_preset: str = field(default_factory=lambda: os.environ.get(
        "VIDTRANS_COMFYUI_IPADAPTER_PRESET", "STANDARD (medium strength)"
    ).strip())
    ipadapter_weight: float = field(default_factory=lambda: float(os.environ.get(
        "VIDTRANS_COMFYUI_IPADAPTER_WEIGHT", "0.7"
    )))
    # Low on purpose: the place should look the same, but the camera and action follow the prompt.
    setting_weight: float = field(default_factory=lambda: float(os.environ.get(
        "VIDTRANS_COMFYUI_SETTING_WEIGHT", "0.35"
    )))

    def __post_init__(self) -> None:
        if not self.base_url or not self.checkpoint:
            raise ImageGenerationError(
                "ComfyUI chưa được cấu hình (VIDTRANS_COMFYUI_URL và VIDTRANS_COMFYUI_CHECKPOINT)"
            )
        self._open = self.opener or urllib.request.urlopen

    @property
    def cache_key(self) -> str:
        return (f"comfyui-{self.checkpoint}-{self.steps}-{self.cfg}-{self.sampler}-{self.scheduler}-"
                f"{self.max_side}-ipadapter-{self.ipadapter_enabled}-{self.ipadapter_preset}-{self.ipadapter_weight}"
                + (f"-setting-{self.setting_weight}" if self.setting_weight != 0.35 else ""))

    def _workflow(self, request: ImageRequest, reference_name: str = "", setting_name: str = "") -> dict[str, Any]:
        # Avoid exhausting consumer VRAM on a 1080x1920 final video. The result
        # is upscaled by fit_cover after generation. Latent dimensions use /8.
        scale = min(1.0, self.max_side / max(request.width, request.height))
        width = max(64, round(request.width * scale) // 8 * 8)
        height = max(64, round(request.height * scale) // 8 * 8)
        negative = request.negative_prompt or "text, watermark, logo, blurry, low quality, malformed anatomy"
        workflow: dict[str, Any] = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": self.checkpoint}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": request.prompt, "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "5": {"class_type": "KSampler", "inputs": {
                "seed": request.seed or int.from_bytes(hashlib.sha256(request.prompt.encode()).digest()[:4], "big"),
                "steps": self.steps, "cfg": self.cfg, "sampler_name": self.sampler,
                "scheduler": self.scheduler, "denoise": 1.0, "model": ["1", 0],
                "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
            }},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "VidTrans", "images": ["6", 0]}},
        }
        if reference_name and self.ipadapter_enabled:
            workflow["8"] = {"class_type": "IPAdapterUnifiedLoader", "inputs": {
                "model": ["1", 0], "preset": self.ipadapter_preset,
            }}
            workflow["9"] = {"class_type": "LoadImage", "inputs": {"image": reference_name}}
            workflow["10"] = {"class_type": "IPAdapter", "inputs": {
                "model": ["8", 0], "ipadapter": ["8", 1], "image": ["9", 0],
                "weight": self.ipadapter_weight, "start_at": 0.0, "end_at": 0.8,
                "weight_type": "prompt is more important",
            }}
            workflow["5"]["inputs"]["model"] = ["10", 0]
        if setting_name and self.ipadapter_enabled:
            if "8" not in workflow:
                workflow["8"] = {"class_type": "IPAdapterUnifiedLoader", "inputs": {
                    "model": ["1", 0], "preset": self.ipadapter_preset,
                }}
            workflow["11"] = {"class_type": "LoadImage", "inputs": {"image": setting_name}}
            workflow["12"] = {"class_type": "IPAdapter", "inputs": {
                "model": workflow["5"]["inputs"]["model"] if "10" in workflow else ["8", 0],
                "ipadapter": ["8", 1], "image": ["11", 0],
                "weight": self.setting_weight, "start_at": 0.0, "end_at": 0.6,
                "weight_type": "prompt is more important",
            }}
            workflow["5"]["inputs"]["model"] = ["12", 0]
        return workflow

    def _upload_reference(self, path: Path) -> str:
        """Upload one local reference to ComfyUI's input directory."""
        path = Path(path)
        suffix = path.suffix.lower() if path.suffix else ".png"
        name = f"vidtrans-{hashlib.sha256(path.read_bytes()).hexdigest()[:20]}{suffix}"
        boundary = f"----VidTrans{uuid.uuid4().hex}"
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput\r\n".encode(),
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
             "Content-Type: image/png\r\n\r\n").encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        request = urllib.request.Request(
            self.base_url + "/upload/image", data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST",
        )
        try:
            with self._open(request, timeout=min(self.timeout, 60.0)) as response:
                uploaded = json.loads(response.read(1024 * 1024).decode("utf-8"))
        except (TimeoutError, urllib.error.URLError, ConnectionError, OSError, ValueError) as exc:
            raise ImageGenerationError("Không tải được ảnh tham chiếu lên ComfyUI") from exc
        uploaded_name = str(uploaded.get("name") or "")
        if not uploaded_name:
            raise ImageGenerationError(f"ComfyUI không nhận ảnh tham chiếu: {uploaded}")
        subfolder = str(uploaded.get("subfolder") or "").strip("/\\")
        return f"{subfolder}/{uploaded_name}" if subfolder else uploaded_name

    def _json(self, path: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(1, self.connect_attempts + 1):
            request = urllib.request.Request(
                self.base_url + path, data=data,
                headers={"Content-Type": "application/json"} if data else {},
                method="POST" if data else "GET",
            )
            try:
                with self._open(request, timeout=min(self.timeout, 30.0)) as response:
                    raw = response.read(16 * 1024 * 1024)
                    # /queue and /interrupt answer 200 with an empty body.
                    return json.loads(raw.decode("utf-8")) if raw.strip() else {}
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read(4096).decode("utf-8", "replace")
                except Exception:
                    pass
                raise ImageGenerationError(f"ComfyUI lỗi HTTP {exc.code}: {detail[:300]}") from exc
            except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
                if attempt < self.connect_attempts:
                    logger.warning("ComfyUI %s failed (%s); retrying %d/%d",
                                   path.split("?")[0], type(exc).__name__, attempt, self.connect_attempts - 1)
                    self.sleeper(2.0 * attempt)
                    continue
                error = ImageGenerationError("Không kết nối được ComfyUI; hãy kiểm tra URL và ứng dụng ComfyUI")
                error.unavailable = True
                raise error from exc
        raise AssertionError("unreachable")

    def _generate_workflow(self, request: ImageRequest, output_path: Path, reference_name: str = "",
                           setting_name: str = "") -> None:
        from PIL import Image

        queued = self._json("/prompt", body={"prompt": self._workflow(request, reference_name, setting_name)})
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            raise ImageGenerationError(f"ComfyUI không nhận workflow: {queued.get('error', 'thiếu prompt_id')}")
        deadline = time.monotonic() + self.timeout
        image_info: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            if self.check_active is not None:
                try:
                    self.check_active()
                except Exception:
                    self._cancel_prompt(str(prompt_id))
                    raise
            history = self._json(f"/history/{urllib.parse.quote(str(prompt_id))}")
            result = history.get(str(prompt_id)) or {}
            status = result.get("status") or {}
            if status.get("status_str") == "error":
                messages = status.get("messages") or []
                raise ImageGenerationError(f"ComfyUI sinh ảnh thất bại: {str(messages[-1] if messages else status)[:500]}")
            for output in (result.get("outputs") or {}).values():
                images = output.get("images") or []
                if images:
                    image_info = images[0]
                    break
            if image_info:
                break
            self.sleeper(self.poll_interval)
        if not image_info:
            raise ImageGenerationError(f"ComfyUI quá thời gian chờ ({self.timeout:.0f} giây)")
        query = urllib.parse.urlencode({
            "filename": image_info["filename"], "subfolder": image_info.get("subfolder", ""),
            "type": image_info.get("type", "output"),
        })
        view = urllib.request.Request(f"{self.base_url}/view?{query}", method="GET")
        try:
            with self._open(view, timeout=min(self.timeout, 60.0)) as response:
                image = Image.open(io.BytesIO(response.read(64 * 1024 * 1024)))
                save_png_atomic(fit_cover(image, request.width, request.height), output_path)
        except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
            raise ImageGenerationError("ComfyUI đã sinh ảnh nhưng VidTrans không tải được ảnh kết quả") from exc

    def _cancel_prompt(self, prompt_id: str) -> None:
        """Drop a queued prompt, or interrupt it when it is the one ComfyUI is running."""
        try:
            self._json("/queue", body={"delete": [prompt_id]})
            running = self._json("/queue").get("queue_running") or []
            # Entries are [number, prompt_id, ...]; never interrupt another client's prompt.
            if any(len(entry) > 1 and entry[1] == prompt_id for entry in running):
                self._json("/interrupt", body={"prompt_id": prompt_id})
        except (ImageGenerationError, ValueError) as exc:
            logger.warning("Could not cancel ComfyUI prompt %s: %s", prompt_id, exc)

    def generate(self, request: ImageRequest, output_path: Path) -> None:
        if self.ipadapter_enabled and (request.references or request.setting_reference):
            try:
                reference_name = self._upload_reference(request.references[0]) if request.references else ""
                setting_name = (self._upload_reference(request.setting_reference)
                                if request.setting_reference and self.setting_weight > 0 else "")
                self._generate_workflow(request, output_path, reference_name, setting_name)
                return
            except ImageGenerationError as exc:
                logger.warning("IP-Adapter failed; retrying the scene with the base checkpoint: %s", exc)
        self._generate_workflow(request, output_path)


@dataclass
class GeminiImageProvider:
    key = "gemini"
    supports_references = True
    timeout: float = 120.0
    attempts: int = 4
    opener: Callable[..., Any] | None = None
    sleeper: Callable[[float], None] = time.sleep
    model: str = field(default_factory=lambda: os.environ.get(
        "VIDTRANS_COMIC_IMAGE_MODEL", DEFAULT_GEMINI_IMAGE_MODEL).strip() or DEFAULT_GEMINI_IMAGE_MODEL)

    def __post_init__(self) -> None:
        self.api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not self.api_key:
            raise ImageGenerationError("Gemini chưa được cấu hình (GEMINI_API_KEY)")
        self._open = self.opener or urllib.request.urlopen

    @property
    def cache_key(self) -> str:
        return f"gemini-{self.model}"

    def _body(self, request: ImageRequest) -> bytes:
        text = request.prompt
        if request.references:
            text += (f"\nThe first {len(request.references)} image(s) show the characters: keep every character's "
                     "face, hairstyle and outfit identical to them. Use them only for identity: draw the full scene "
                     "described above, not a portrait, and do not copy their pose or plain background.")
        if request.setting_reference:
            text += ("\nThe last image shows the location: keep its architecture, materials, colours and fixed "
                     "props, but frame it with the camera angle and action described above. Ignore anyone in it.")
        if request.negative_prompt:
            text += f"\nAvoid: {request.negative_prompt}."
        parts: list[dict[str, Any]] = [{"text": text}]
        for reference in (*request.references, *((request.setting_reference,) if request.setting_reference else ())):
            parts.append({"inline_data": {
                "mime_type": "image/png",
                "data": base64.b64encode(Path(reference).read_bytes()).decode("ascii"),
            }})
        config: dict[str, Any] = {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": request.aspect_ratio},
        }
        if request.seed:
            config["seed"] = request.seed
        return json.dumps({"contents": [{"role": "user", "parts": parts}], "generationConfig": config}).encode()

    def generate(self, request: ImageRequest, output_path: Path) -> None:
        from PIL import Image

        body = self._body(request)
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            http_request = urllib.request.Request(
                f"{GEMINI_BASE_URL}/{self.model}:generateContent",
                data=body,
                headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
                method="POST",
            )
            try:
                with self._open(http_request, timeout=self.timeout) as response:
                    payload = json.loads(response.read(64 * 1024 * 1024).decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise ImageGenerationError(f"Gemini không có model ảnh {self.model}") from exc
                if exc.code not in {429, 500, 502, 503, 504}:
                    error = ImageGenerationError(f"Gemini Image lỗi HTTP {exc.code}")
                    error.unavailable = exc.code in {401, 403}  # A bad key fails every image.
                    raise error from exc
                last_error = exc
            except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
                last_error = exc
            if attempt + 1 < self.attempts:
                self.sleeper(min(60.0, 5.0 * 2 ** attempt))
        else:
            error = ImageGenerationError(
                "Gemini Image hết hạn mức hoặc không phản hồi sau nhiều lần thử"
                f" ({type(last_error).__name__ if last_error else 'unknown'})"
            )
            error.rate_limited = isinstance(last_error, urllib.error.HTTPError) and last_error.code == 429
            error.unavailable = True
            raise error from last_error
        for candidate in payload.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    image = Image.open(io.BytesIO(base64.b64decode(inline["data"])))
                    save_png_atomic(fit_cover(image, request.width, request.height), output_path)
                    return
        reason = ((payload.get("candidates") or [{}])[0].get("finishReason")
                  or (payload.get("promptFeedback") or {}).get("blockReason") or "no_image")
        raise ImageGenerationError(f"Gemini không trả về ảnh ({reason}); hãy sửa prompt của cảnh")


def _composition_hint(request: ImageRequest) -> str:
    # Square-only models are cropped by fit_cover: keep the subject in the part that survives.
    orientation = "vertical" if request.height > request.width else "horizontal"
    return f"{request.prompt}\n{orientation} {request.aspect_ratio} composition, main subject centered"


def _decode_image(raw: bytes, content_type: str):
    """Cloudflare answers raw image bytes (SDXL) or JSON with a base64 image (FLUX)."""
    from PIL import Image

    if not content_type.startswith("application/json"):
        return Image.open(io.BytesIO(raw))
    payload = json.loads(raw.decode("utf-8"))
    encoded = (payload.get("result") or {}).get("image") or payload.get("image")
    if not encoded:
        raise ImageGenerationError(f"Không nhận được ảnh: {str(payload.get('errors') or payload)[:300]}")
    return Image.open(io.BytesIO(base64.b64decode(encoded)))


def _fetch_image(open_url: Callable[..., Any], build_request: Callable[[], urllib.request.Request], *,
                 label: str, timeout: float, attempts: int, sleeper: Callable[[float], None]):
    """Send one image request, retrying rate limits and transient errors with backoff."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with open_url(build_request(), timeout=timeout) as response:
                content_type = (response.headers.get("Content-Type") or "") if getattr(response, "headers", None) else ""
                return _decode_image(response.read(64 * 1024 * 1024), content_type)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(2048).decode("utf-8", "replace")
            except Exception:
                pass
            # Pollinations' free tier answers 402 FREE_LIMIT to a call that came too soon.
            throttled = exc.code == 402 and "FREE_LIMIT" in detail
            if exc.code not in {429, 500, 502, 503, 504, 530} and not throttled:
                error = ImageGenerationError(f"{label} lỗi HTTP {exc.code}: {detail[:300]}")
                error.unavailable = exc.code in {401, 402, 403}  # A bad key/quota fails every image.
                raise error from exc
            last_error = exc
            retry_after = _RETRY_AFTER_RE.search(detail)
        except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
            last_error = exc
            retry_after = None
        except OSError as exc:  # PIL could not read the body
            raise ImageGenerationError(f"{label} trả về dữ liệu không phải ảnh") from exc
        if attempt + 1 < attempts:
            delay = min(60.0, float(retry_after.group(1)) + 1.0) if retry_after else min(60.0, 5.0 * 2 ** attempt)
            logger.warning("%s failed (%s); retrying in %.0fs", label, type(last_error).__name__, delay)
            sleeper(delay)
    error = ImageGenerationError(
        f"{label} hết hạn mức hoặc không phản hồi sau nhiều lần thử"
        f" ({type(last_error).__name__ if last_error else 'unknown'})"
    )
    error.rate_limited = isinstance(last_error, urllib.error.HTTPError) and last_error.code in {402, 429}
    error.unavailable = True
    raise error from last_error


@dataclass
class CloudflareImageProvider:
    """Cloudflare Workers AI text-to-image (free tier: 10,000 Neurons per day).

    FLUX.1 schnell only draws square 1024px images, so the result is cropped to
    the scene size; SDXL-family models take width/height and a negative prompt.
    """

    key = "cloudflare"
    supports_references = False
    timeout: float = 120.0
    attempts: int = 4
    opener: Callable[..., Any] | None = None
    sleeper: Callable[[float], None] = time.sleep
    account_id: str = field(default_factory=lambda: os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip())
    api_token: str = field(default_factory=lambda: os.environ.get("CLOUDFLARE_API_TOKEN", "").strip())
    model: str = field(default_factory=lambda: os.environ.get(
        "VIDTRANS_CLOUDFLARE_IMAGE_MODEL", DEFAULT_CLOUDFLARE_IMAGE_MODEL).strip() or DEFAULT_CLOUDFLARE_IMAGE_MODEL)
    steps: int = field(default_factory=lambda: int(os.environ.get("VIDTRANS_CLOUDFLARE_IMAGE_STEPS", "4")))

    def __post_init__(self) -> None:
        if not self.account_id or not self.api_token:
            raise ImageGenerationError(
                "Cloudflare Workers AI chưa được cấu hình (CLOUDFLARE_ACCOUNT_ID và CLOUDFLARE_API_TOKEN)"
            )
        self._open = self.opener or urllib.request.urlopen

    @property
    def cache_key(self) -> str:
        return f"cloudflare-{self.model}-{self.steps}"

    def _body(self, request: ImageRequest) -> dict[str, Any]:
        seed = request.seed or int.from_bytes(hashlib.sha256(request.prompt.encode()).digest()[:4], "big")
        if "flux-1-schnell" in self.model:
            return {"prompt": _composition_hint(request)[:2048], "steps": max(1, min(8, self.steps)), "seed": seed}
        # SDXL-family models: sizes must be multiples of 8 within 256..2048.
        scale = min(1.0, 1024 / max(request.width, request.height))
        return {
            "prompt": request.prompt[:2048], "seed": seed, "num_steps": max(1, min(20, self.steps)),
            "negative_prompt": request.negative_prompt or "text, watermark, logo, blurry, low quality",
            "width": max(256, round(request.width * scale) // 8 * 8),
            "height": max(256, round(request.height * scale) // 8 * 8),
        }

    def generate(self, request: ImageRequest, output_path: Path) -> None:
        body = json.dumps(self._body(request)).encode()
        url = f"{CLOUDFLARE_BASE_URL}/{urllib.parse.quote(self.account_id)}/ai/run/{self.model}"
        image = _fetch_image(
            self._open,
            lambda: urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {self.api_token}",
            }),
            label="Cloudflare Workers AI", timeout=self.timeout, attempts=self.attempts, sleeper=self.sleeper,
        )
        save_png_atomic(fit_cover(image, request.width, request.height), output_path)


@dataclass
class PollinationsImageProvider:
    """Pollinations.ai: free, works without an account.

    Anonymous calls are throttled (about one image per 15 s), may be served by a
    smaller model and carry a watermark; POLLINATIONS_TOKEN lifts those limits.
    """

    key = "pollinations"
    supports_references = False
    timeout: float = 180.0
    attempts: int = 4
    opener: Callable[..., Any] | None = None
    sleeper: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    token: str = field(default_factory=lambda: os.environ.get("POLLINATIONS_TOKEN", "").strip())
    model: str = field(default_factory=lambda: os.environ.get("VIDTRANS_POLLINATIONS_MODEL", "flux").strip() or "flux")

    def __post_init__(self) -> None:
        self._open = self.opener or urllib.request.urlopen

    @property
    def cache_key(self) -> str:
        return f"pollinations-{self.model}"

    def _url(self, request: ImageRequest) -> str:
        scale = min(1.0, 1024 / max(request.width, request.height))
        prompt = request.prompt
        if request.negative_prompt:
            prompt += f"\nAvoid: {request.negative_prompt}."
        query = {
            "width": max(64, round(request.width * scale) // 8 * 8),
            "height": max(64, round(request.height * scale) // 8 * 8),
            "seed": (request.seed or int.from_bytes(hashlib.sha256(request.prompt.encode()).digest()[:4], "big"))
            % POLLINATIONS_MAX_SEED,
            "model": self.model, "nologo": "true", "private": "true",
        }
        # Prompts travel in the URL path; keep them under common URL length limits.
        return f"{POLLINATIONS_BASE_URL}/{urllib.parse.quote(prompt[:1500], safe='')}?{urllib.parse.urlencode(query)}"

    def generate(self, request: ImageRequest, output_path: Path) -> None:
        url = self._url(request)
        headers = {"User-Agent": "VidTrans/1.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            image = self._fetch(url, headers)
        else:
            # One anonymous call at a time across every job, spaced by the free-tier interval.
            with _POLLINATIONS_LOCK:
                wait = _pollinations_last_call[0] + POLLINATIONS_FREE_INTERVAL_SECONDS - self.clock()
                if wait > 0:
                    self.sleeper(wait)
                try:
                    image = self._fetch(url, headers)
                finally:
                    _pollinations_last_call[0] = self.clock()
        save_png_atomic(fit_cover(image, request.width, request.height), output_path)


    def _fetch(self, url: str, headers: dict[str, str]):
        return _fetch_image(
            self._open, lambda: urllib.request.Request(url, headers=headers, method="GET"),
            label="Pollinations", timeout=self.timeout, attempts=self.attempts, sleeper=self.sleeper,
        )


def image_provider_statuses() -> list[dict[str, Any]]:
    return [
        {"key": "placeholder", "label": "Ảnh minh họa tạm (offline, miễn phí)", "configured": True},
        {"key": "comfyui", "label": "ComfyUI local (AI, miễn phí)",
         "configured": bool(os.environ.get("VIDTRANS_COMFYUI_URL", "").strip()
                            and os.environ.get("VIDTRANS_COMFYUI_CHECKPOINT", "").strip())},
        {"key": "cloudflare", "label": "Cloudflare Workers AI FLUX (AI, miễn phí theo ngày)",
         "configured": bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
                            and os.environ.get("CLOUDFLARE_API_TOKEN", "").strip())},
        {"key": "pollinations", "label": "Pollinations (AI, miễn phí, chậm)", "configured": True},
        {"key": "gemini", "label": "Gemini Image (AI, giữ nhân vật nhất quán)",
         "configured": bool(os.environ.get("GEMINI_API_KEY", "").strip())},
    ]


# Providers that take reference pictures (characters, places); the others draw from the prompt alone.
REFERENCE_PROVIDERS = frozenset({ComfyUIImageProvider.key, GeminiImageProvider.key})


def create_image_provider(key: str, *, font_path: Path | None = None) -> ImageProvider:
    if key == "comfyui":
        return ComfyUIImageProvider()
    if key == "cloudflare":
        return CloudflareImageProvider()
    if key == "pollinations":
        return PollinationsImageProvider()
    if key == "gemini":
        return GeminiImageProvider()
    if key == "placeholder":
        return PlaceholderImageProvider(font_path)
    raise ValueError(f"image_provider không hợp lệ: {key}")
