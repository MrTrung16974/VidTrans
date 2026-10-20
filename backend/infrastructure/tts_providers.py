from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape

TTS_PROVIDERS = ("edge", "azure", "elevenlabs")

ELEVENLABS_ATTEMPTS = 5
ELEVENLABS_RETRY_BASE_SECONDS = 1.0
# 409 already_running: a Voice Library voice is added to the account on first
# use, and concurrent first uses of the same voice conflict. 429: rate limit.
ELEVENLABS_RETRYABLE_STATUSES = frozenset({409, 429})

_elevenlabs_ready_voices: set[str] = set()
_elevenlabs_voice_locks: dict[str, threading.Lock] = {}
_elevenlabs_locks_guard = threading.Lock()


class TTSProviderHTTPError(RuntimeError):
    def __init__(self, provider_label: str, status: int, detail: str) -> None:
        super().__init__(f"{provider_label} API lỗi HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class TTSProviderStatus:
    key: str
    label: str
    configured: bool
    free_note: str
    female_voice_label: str = "Giọng nữ"
    male_voice_label: str = "Giọng nam"
    supports_pitch_matching: bool = True


def normalize_tts_provider(value: str | None) -> str:
    provider = (value or "edge").strip().lower()
    if provider not in TTS_PROVIDERS:
        raise ValueError(f"tts_provider must be one of: {', '.join(TTS_PROVIDERS)}")
    return provider


def provider_statuses() -> list[TTSProviderStatus]:
    return [
        TTSProviderStatus("edge", "Microsoft Edge TTS", True, "Miễn phí, không cần API key", "Nữ — Hoài My", "Nam — Nam Minh"),
        TTSProviderStatus("azure", "Azure AI Speech", bool(os.environ.get("VIDTRANS_AZURE_SPEECH_KEY") and os.environ.get("VIDTRANS_AZURE_SPEECH_REGION")), "API chính thức; Azure có hạn mức F0 miễn phí", "Nữ — Hoài My", "Nam — Nam Minh"),
        TTSProviderStatus(
            "elevenlabs",
            "ElevenLabs",
            all(os.environ.get(name) for name in (
                "VIDTRANS_ELEVENLABS_API_KEY",
                "VIDTRANS_ELEVENLABS_FEMALE_VOICE_ID",
                "VIDTRANS_ELEVENLABS_MALE_VOICE_ID",
            )),
            "Giọng tự nhiên; gói Free có credit hằng tháng",
            os.environ.get("VIDTRANS_ELEVENLABS_FEMALE_VOICE_LABEL", "Nữ — ElevenLabs"),
            os.environ.get("VIDTRANS_ELEVENLABS_MALE_VOICE_LABEL", "Nam — ElevenLabs"),
            False,
        ),
    ]


def ensure_provider_configured(provider: str) -> None:
    normalized = normalize_tts_provider(provider)
    status = next(item for item in provider_statuses() if item.key == normalized)
    if status.configured:
        return
    if normalized == "azure":
        raise ValueError("Azure Speech chưa được cấu hình: cần VIDTRANS_AZURE_SPEECH_KEY và VIDTRANS_AZURE_SPEECH_REGION")
    raise ValueError("ElevenLabs chưa được cấu hình: cần API key và voice ID nam/nữ trong biến môi trường VIDTRANS_ELEVENLABS_*")


def synthesize_azure(text: str, output_path: Path, *, voice_name: str, rate_percent: int, pitch_hz: int) -> None:
    ensure_provider_configured("azure")
    region = os.environ["VIDTRANS_AZURE_SPEECH_REGION"].strip()
    endpoint = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    language = "-".join(voice_name.split("-")[:2])
    ssml = (f'<speak version="1.0" xml:lang="{escape(language)}"><voice name="{escape(voice_name)}">'
            f'<prosody rate="{rate_percent:+d}%" pitch="{pitch_hz:+d}Hz">{escape(text)}</prosody>'
            "</voice></speak>").encode("utf-8")
    request = Request(endpoint, data=ssml, method="POST", headers={
        "Ocp-Apim-Subscription-Key": os.environ["VIDTRANS_AZURE_SPEECH_KEY"],
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        "User-Agent": "VidTrans",
    })
    _write_response(request, output_path, "Azure Speech")


def synthesize_elevenlabs(
    text: str,
    output_path: Path,
    *,
    voice_type: str,
    voice_style: str = "natural",
    speed: float = 1.0,
) -> None:
    ensure_provider_configured("elevenlabs")
    voice_env = "VIDTRANS_ELEVENLABS_MALE_VOICE_ID" if voice_type == "male" else "VIDTRANS_ELEVENLABS_FEMALE_VOICE_ID"
    voice_id = os.environ.get(voice_env, "").strip()
    if not voice_id:
        raise ValueError(f"ElevenLabs chưa có voice ID: cần {voice_env}")
    style_settings = {
        "natural": (0.50, 0.00), "warm": (0.62, 0.10), "bright": (0.42, 0.18),
        "deep": (0.68, 0.08), "energetic": (0.32, 0.30), "gentle": (0.75, 0.05),
        "storyteller": (0.52, 0.22), "news": (0.72, 0.00), "commercial": (0.40, 0.28),
        "youthful": (0.38, 0.22), "authoritative": (0.78, 0.04), "calm": (0.82, 0.00),
        "cheerful": (0.32, 0.30),
    }
    stability, style = style_settings.get(voice_style, style_settings["natural"])
    payload = json.dumps({
        "text": text,
        "model_id": os.environ.get("VIDTRANS_ELEVENLABS_MODEL_ID", "eleven_flash_v2_5").strip(),
        "voice_settings": {
            "stability": stability,
            "similarity_boost": 0.75,
            "style": style,
            "speed": max(0.7, min(1.2, speed)),
            "use_speaker_boost": True,
        },
    }, ensure_ascii=False).encode("utf-8")
    request = Request(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128", data=payload, method="POST", headers={
        "xi-api-key": os.environ["VIDTRANS_ELEVENLABS_API_KEY"],
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    })
    if voice_id in _elevenlabs_ready_voices:
        _write_elevenlabs_with_retry(request, output_path)
        return
    # Let only the first request for a voice through so ElevenLabs adds a
    # library voice to the account once; parallel cues wait, then run freely.
    with _elevenlabs_locks_guard:
        voice_lock = _elevenlabs_voice_locks.setdefault(voice_id, threading.Lock())
    with voice_lock:
        if voice_id not in _elevenlabs_ready_voices:
            _write_elevenlabs_with_retry(request, output_path)
            _elevenlabs_ready_voices.add(voice_id)
            return
    _write_elevenlabs_with_retry(request, output_path)


def _write_elevenlabs_with_retry(request: Request, output_path: Path) -> None:
    for attempt in range(1, ELEVENLABS_ATTEMPTS + 1):
        try:
            _write_response(request, output_path, "ElevenLabs")
            return
        except TTSProviderHTTPError as exc:
            if exc.status not in ELEVENLABS_RETRYABLE_STATUSES or attempt == ELEVENLABS_ATTEMPTS:
                raise
            delay = ELEVENLABS_RETRY_BASE_SECONDS * 2 ** (attempt - 1)
            time.sleep(delay + random.uniform(0, delay / 2))


def _write_response(request: Request, output_path: Path, provider_label: str) -> None:
    try:
        with urlopen(request, timeout=45) as response:
            audio = response.read()
    except HTTPError as exc:
        detail = exc.read(400).decode("utf-8", errors="replace")
        raise TTSProviderHTTPError(provider_label, exc.code, detail) from exc
    except URLError as exc:
        raise RuntimeError(f"Không thể kết nối {provider_label}: {exc.reason}") from exc
    if not audio:
        raise RuntimeError(f"{provider_label} không trả về dữ liệu âm thanh")
    output_path.write_bytes(audio)
