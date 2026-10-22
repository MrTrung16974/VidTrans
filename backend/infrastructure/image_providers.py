"""Scene image generators behind one interface.

``placeholder`` draws a styled card locally so the whole pipeline runs without a
GPU or API key; ``gemini`` calls the Gemini image model and accepts character
reference images for consistency. Every provider writes a PNG of exactly the
requested size, so the renderer never has to care which one produced it.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import textwrap
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"


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
        draw.text((margin, height - margin * 2.2), "Ảnh minh họa tạm — chọn Gemini để sinh ảnh AI",
                  font=body_font, fill=(255, 255, 255))
        save_png_atomic(image, output_path)


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
            text += ("\nKeep every character's face, hairstyle and outfit identical to the reference "
                     "image(s) provided.")
        if request.negative_prompt:
            text += f"\nAvoid: {request.negative_prompt}."
        parts: list[dict[str, Any]] = [{"text": text}]
        for reference in request.references:
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


def image_provider_statuses() -> list[dict[str, Any]]:
    return [
        {"key": "placeholder", "label": "Ảnh minh họa tạm (offline, miễn phí)", "configured": True},
        {"key": "gemini", "label": "Gemini Image (AI, giữ nhân vật nhất quán)",
         "configured": bool(os.environ.get("GEMINI_API_KEY", "").strip())},
    ]


def create_image_provider(key: str, *, font_path: Path | None = None) -> ImageProvider:
    if key == "gemini":
        return GeminiImageProvider()
    if key == "placeholder":
        return PlaceholderImageProvider(font_path)
    raise ValueError(f"image_provider không hợp lệ: {key}")
