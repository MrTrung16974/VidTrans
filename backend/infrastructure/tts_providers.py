from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

TTS_PROVIDERS = ("edge", "azure", "elevenlabs", "cit")
VOICE_GENDERS = ("female", "male")

ELEVENLABS_ATTEMPTS = 5
ELEVENLABS_RETRY_BASE_SECONDS = 1.0
# 409 already_running: a Voice Library voice is added to the account on first
# use, and concurrent first uses of the same voice conflict. 429: rate limit.
ELEVENLABS_RETRYABLE_STATUSES = frozenset({409, 429})

ELEVENLABS_PAID_PLAN_MESSAGE = (
    "Tài khoản ElevenLabs gói Free không được dùng giọng trong Voice Library qua API. "
    "Nâng cấp ElevenLabs lên gói Starter trở lên, hoặc chỉ khai báo giọng mặc định (premade) "
    "/ giọng tự tạo trong tài khoản vào VIDTRANS_ELEVENLABS_VOICES, hoặc chọn Edge TTS."
)
ELEVENLABS_PREVIEW_MAX_BYTES = 5 * 1024 * 1024
# Preview clips are served from ElevenLabs' own storage; refuse anything else.
ELEVENLABS_PREVIEW_HOSTS = ("storage.googleapis.com", "elevenlabs.io")

_elevenlabs_previews: dict[str, tuple[bytes, str]] = {}
_elevenlabs_previews_lock = threading.Lock()
_elevenlabs_ready_voices: set[str] = set()
_elevenlabs_voice_locks: dict[str, threading.Lock] = {}
_elevenlabs_locks_guard = threading.Lock()

# CIT Voice Studio is a desktop app (VieNeu-TTS) exposing a local HTTP API; it has no
# accounts or credits, but it is CPU-bound and only reachable while the app is open.
CIT_SPEED_RANGE = (0.75, 1.5)
CIT_STATUS_TTL_SECONDS = 15.0
# The app answers no request while it synthesises a passage, so a timed-out poll means "busy":
# the last good voice list is kept. Other failures (app closed) keep it only briefly.
CIT_VOICES_TIMEOUT_SECONDS = 5
CIT_STALE_SECONDS = 120.0
CIT_GENERATE_TIMEOUT_SECONDS = 600
CIT_GENERATE_ATTEMPTS = 2
CIT_RETRY_BASE_SECONDS = 0.5
_cit_cache: dict[str, tuple[float, object]] = {}
_cit_cache_lock = threading.Lock()
_cit_semaphore: threading.BoundedSemaphore | None = None
_cit_semaphore_guard = threading.Lock()
# Keep the on-demand sample deliberately short. VieNeu generates on CPU and does not
# stream its response, so every extra sentence is added directly to the first-play wait.
VOICE_PREVIEW_TEXT = "Xin chào, mời bạn nghe thử giọng này."
_cit_previews: dict[str, tuple[bytes, str]] = {}
_cit_previews_lock = threading.Lock()
_cit_preview_voice_locks: dict[str, threading.Lock] = {}
_cit_preview_locks_guard = threading.Lock()


def _cit_preview_lock(voice_key: str) -> threading.Lock:
    """Serialize cache misses for one voice without blocking other cached voices."""

    with _cit_preview_locks_guard:
        return _cit_preview_voice_locks.setdefault(voice_key, threading.Lock())


def _cit_preview_cache_path(voice_key: str) -> Path:
    # backend/work is a persistent Docker volume in the standard deployment. The
    # digest also invalidates old clips whenever the sample sentence changes.
    cache_root = Path(
        os.environ.get(
            "VIDTRANS_TTS_PREVIEW_CACHE_DIR",
            str(Path(__file__).resolve().parents[1] / "work" / "voice-previews"),
        )
    )
    digest = hashlib.sha256(f"cit\0{voice_key}\0{VOICE_PREVIEW_TEXT}".encode("utf-8")).hexdigest()
    return cache_root / f"{digest}.audio"


class TTSProviderHTTPError(RuntimeError):
    def __init__(self, provider_label: str, status: int, detail: str) -> None:
        super().__init__(f"{provider_label} API lỗi HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class TTSProviderUnavailable(RuntimeError):
    """The provider cannot be reached at all (e.g. a local app is closed)."""


@dataclass(frozen=True)
class TTSProviderStatus:
    key: str
    label: str
    configured: bool
    free_note: str
    female_voice_label: str = "Giọng nữ"
    male_voice_label: str = "Giọng nam"
    supports_pitch_matching: bool = True
    voices: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ElevenLabsVoice:
    key: str
    label: str
    gender: str
    voice_id: str

    def public(self) -> dict[str, str]:
        # The voice ID stays on the server; clients select voices by key.
        return {"key": self.key, "label": self.label, "gender": self.gender}


def _elevenlabs_voice_key(voice_id: str) -> str:
    return "v" + hashlib.sha256(voice_id.encode("utf-8")).hexdigest()[:10]


def elevenlabs_voices() -> list[ElevenLabsVoice]:
    """Return voices from VIDTRANS_ELEVENLABS_VOICES plus the legacy pair.

    Each entry is ``label|female|voice_id`` or ``label|male|voice_id``,
    separated by newlines or semicolons. Malformed entries are skipped with a
    warning so one typo does not disable the provider.
    """

    voices: list[ElevenLabsVoice] = []
    seen: set[str] = set()

    def add(label: str, gender: str, voice_id: str) -> None:
        if voice_id in seen:
            return
        seen.add(voice_id)
        voices.append(ElevenLabsVoice(_elevenlabs_voice_key(voice_id), label, gender, voice_id))

    raw = os.environ.get("VIDTRANS_ELEVENLABS_VOICES", "")
    for position, entry in enumerate(raw.replace(";", "\n").splitlines(), start=1):
        entry = entry.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split("|")]
        if len(parts) != 3 or not parts[0] or parts[1].lower() not in VOICE_GENDERS or not parts[2]:
            logger.warning("Ignoring malformed VIDTRANS_ELEVENLABS_VOICES entry #%d", position)
            continue
        add(parts[0], parts[1].lower(), parts[2])
    for gender, default_label in (("female", "Nữ — ElevenLabs"), ("male", "Nam — ElevenLabs")):
        voice_id = os.environ.get(f"VIDTRANS_ELEVENLABS_{gender.upper()}_VOICE_ID", "").strip()
        if voice_id:
            label = os.environ.get(f"VIDTRANS_ELEVENLABS_{gender.upper()}_VOICE_LABEL", default_label)
            add(label, gender, voice_id)
    return voices


def _default_elevenlabs_voice(voices: list[ElevenLabsVoice], gender: str) -> ElevenLabsVoice | None:
    return next((voice for voice in voices if voice.gender == gender), voices[0] if voices else None)


def resolve_elevenlabs_voice_selection(
    female_key: str | None = None, male_key: str | None = None
) -> dict[str, str]:
    """Validate the voices chosen in the form, filling gaps with defaults."""

    voices = elevenlabs_voices()
    by_key = {voice.key: voice for voice in voices}
    selection: dict[str, str] = {}
    for gender, key in (("female", female_key), ("male", male_key)):
        key = (key or "").strip()
        if key and key not in by_key:
            raise ValueError(f"Giọng ElevenLabs đã chọn không còn trong cấu hình máy chủ: {key}")
        voice = by_key.get(key) or _default_elevenlabs_voice(voices, gender)
        if voice is not None:
            selection[gender] = voice.key
    return selection


def normalize_tts_provider(value: str | None) -> str:
    provider = (value or "edge").strip().lower()
    if provider not in TTS_PROVIDERS:
        raise ValueError(f"tts_provider must be one of: {', '.join(TTS_PROVIDERS)}")
    return provider


def default_tts_provider() -> str:
    """CIT Voice Studio when its app is reachable, otherwise the always-available Edge TTS."""

    return "cit" if cit_voices() else "edge"


def provider_statuses() -> list[TTSProviderStatus]:
    return [
        TTSProviderStatus("edge", "Microsoft Edge TTS", True, "Miễn phí, không cần API key", "Nữ — Hoài My", "Nam — Nam Minh"),
        TTSProviderStatus("azure", "Azure AI Speech", bool(os.environ.get("VIDTRANS_AZURE_SPEECH_KEY") and os.environ.get("VIDTRANS_AZURE_SPEECH_REGION")), "API chính thức; Azure có hạn mức F0 miễn phí", "Nữ — Hoài My", "Nam — Nam Minh"),
        _elevenlabs_status(),
        _cit_status(),
    ]


def _elevenlabs_status() -> TTSProviderStatus:
    voices = elevenlabs_voices()
    female = _default_elevenlabs_voice(voices, "female")
    male = _default_elevenlabs_voice(voices, "male")
    return TTSProviderStatus(
        "elevenlabs",
        "ElevenLabs",
        bool(os.environ.get("VIDTRANS_ELEVENLABS_API_KEY") and voices),
        "Giọng tự nhiên; gói Free có credit hằng tháng",
        female.label if female else "Nữ — ElevenLabs",
        male.label if male else "Nam — ElevenLabs",
        False,
        [voice.public() for voice in voices],
    )


def ensure_provider_configured(provider: str) -> None:
    normalized = normalize_tts_provider(provider)
    status = next(item for item in provider_statuses() if item.key == normalized)
    if status.configured:
        return
    if normalized == "azure":
        raise ValueError("Azure Speech chưa được cấu hình: cần VIDTRANS_AZURE_SPEECH_KEY và VIDTRANS_AZURE_SPEECH_REGION")
    if normalized == "cit":
        if not cit_base_url():
            raise ValueError("CIT Voice Studio chưa được cấu hình: cần VIDTRANS_CIT_VOICE_URL")
        raise ValueError(
            f"Không kết nối được CIT Voice Studio tại {cit_base_url()}: hãy mở ứng dụng trên máy chủ "
            "(hoặc bật \"Cho máy khác truy cập API\" và đặt VIDTRANS_CIT_VOICE_API_KEY)"
        )
    raise ValueError(
        "ElevenLabs chưa được cấu hình: cần VIDTRANS_ELEVENLABS_API_KEY và ít nhất một giọng "
        "trong VIDTRANS_ELEVENLABS_VOICES (hoặc VIDTRANS_ELEVENLABS_FEMALE/MALE_VOICE_ID)"
    )


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
    voice_key: str | None = None,
) -> None:
    ensure_provider_configured("elevenlabs")
    voices = elevenlabs_voices()
    voice = next((item for item in voices if item.key == voice_key), None) if voice_key else None
    if voice_key and voice is None:
        raise ValueError("Giọng ElevenLabs của job không còn trong cấu hình máy chủ")
    voice = voice or _default_elevenlabs_voice(voices, "male" if voice_type == "male" else "female")
    voice_id = voice.voice_id
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
            if exc.status == 402 and "paid_plan_required" in exc.detail:
                raise RuntimeError(ELEVENLABS_PAID_PLAN_MESSAGE) from exc
            if exc.status not in ELEVENLABS_RETRYABLE_STATUSES or attempt == ELEVENLABS_ATTEMPTS:
                raise
            delay = ELEVENLABS_RETRY_BASE_SECONDS * 2 ** (attempt - 1)
            time.sleep(delay + random.uniform(0, delay / 2))


# Account-level failures: every remaining line would fail the same way, so the job
# should switch provider instead of failing (out of credits, bad key, rate limit after retries).
TTS_ACCOUNT_BLOCK_STATUSES = frozenset({401, 402, 403, 429})


def tts_account_blocked(error: BaseException | None) -> str | None:
    """A short reason when ``error`` (or its cause) means the paid TTS account cannot be used."""
    seen = 0
    while error is not None and seen < 5:
        if isinstance(error, TTSProviderHTTPError) and error.status in TTS_ACCOUNT_BLOCK_STATUSES:
            detail = error.detail.lower()
            if "quota" in detail or "credits" in detail:
                return "hết credit/hạn mức"
            if error.status == 429:
                return "bị giới hạn tần suất"
            return f"từ chối API key (HTTP {error.status})"
        if isinstance(error, RuntimeError) and str(error) == ELEVENLABS_PAID_PLAN_MESSAGE:
            return "gói Free không dùng được giọng đã chọn"
        if isinstance(error, TTSProviderUnavailable):
            return "không kết nối được"
        error = error.__cause__
        seen += 1
    return None


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


def elevenlabs_voice_preview(voice_key: str) -> tuple[bytes, str]:
    """Return ElevenLabs' own preview clip for a configured voice.

    The clip costs no credits. It is cached in memory because the six-ish
    configured voices are previewed repeatedly while users pick one.
    """

    ensure_provider_configured("elevenlabs")
    voice = next((item for item in elevenlabs_voices() if item.key == voice_key), None)
    if voice is None:
        raise LookupError("Giọng ElevenLabs không có trong cấu hình máy chủ")
    with _elevenlabs_previews_lock:
        cached = _elevenlabs_previews.get(voice.voice_id)
    if cached is not None:
        return cached
    preview_url = _elevenlabs_preview_url(voice)
    host = (urlparse(preview_url).hostname or "").lower()
    if urlparse(preview_url).scheme != "https" or not any(
        host == allowed or host.endswith("." + allowed) for allowed in ELEVENLABS_PREVIEW_HOSTS
    ):
        raise RuntimeError("ElevenLabs trả về đường dẫn nghe thử không hợp lệ")
    try:
        with urlopen(Request(preview_url, headers={"User-Agent": "VidTrans"}), timeout=30) as response:
            audio = response.read(ELEVENLABS_PREVIEW_MAX_BYTES + 1)
            content_type = response.headers.get_content_type() or "audio/mpeg"
    except (HTTPError, URLError) as exc:
        raise RuntimeError("Không tải được bản nghe thử từ ElevenLabs") from exc
    if not audio or len(audio) > ELEVENLABS_PREVIEW_MAX_BYTES:
        raise RuntimeError("Bản nghe thử ElevenLabs rỗng hoặc quá lớn")
    if not content_type.startswith("audio/"):
        content_type = "audio/mpeg"
    with _elevenlabs_previews_lock:
        _elevenlabs_previews[voice.voice_id] = (audio, content_type)
    return audio, content_type


def _elevenlabs_get_json(url: str) -> dict:
    request = Request(url, headers={
        "xi-api-key": os.environ["VIDTRANS_ELEVENLABS_API_KEY"],
        "Accept": "application/json",
    })
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read(2 * 1024 * 1024))


def _elevenlabs_preview_url(voice: ElevenLabsVoice) -> str:
    # Voices in "My Voices" are returned by /v1/voices/{id}; Voice Library
    # voices not yet added to the account are only found in shared-voices.
    try:
        payload = _elevenlabs_get_json(f"https://api.elevenlabs.io/v1/voices/{quote(voice.voice_id, safe='')}")
        if payload.get("preview_url"):
            return str(payload["preview_url"])
    except HTTPError as exc:
        if exc.code not in (400, 404):
            raise RuntimeError(f"ElevenLabs API lỗi HTTP {exc.code} khi lấy thông tin giọng") from exc
    except URLError as exc:
        raise RuntimeError(f"Không thể kết nối ElevenLabs: {exc.reason}") from exc
    try:
        payload = _elevenlabs_get_json(
            "https://api.elevenlabs.io/v1/shared-voices?page_size=100&search=" + quote(voice.label)
        )
    except (HTTPError, URLError) as exc:
        raise RuntimeError("Không tìm được bản nghe thử của giọng trên ElevenLabs") from exc
    for item in payload.get("voices") or []:
        if item.get("voice_id") == voice.voice_id and item.get("preview_url"):
            return str(item["preview_url"])
    raise LookupError("ElevenLabs không có bản nghe thử cho giọng này")


@dataclass(frozen=True)
class CITVoice:
    key: str
    label: str
    gender: str

    def public(self) -> dict[str, str]:
        return {"key": self.key, "label": self.label, "gender": self.gender}


def cit_base_url() -> str:
    return os.environ.get("VIDTRANS_CIT_VOICE_URL", "").strip().rstrip("/")


def _cit_request(path: str, payload: dict | None = None) -> Request:
    headers = {"Accept": "*/*", "User-Agent": "VidTrans"}
    api_key = os.environ.get("VIDTRANS_CIT_VOICE_API_KEY", "").strip()
    if api_key:
        # Only required when the app's "allow other machines" LAN mode is on.
        headers["X-API-Key"] = api_key
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    return Request(cit_base_url() + path, data=data, method="POST" if data else "GET", headers=headers)


def _cit_voice_gender(item: dict) -> str:
    text = " ".join(str(item.get(field) or "") for field in ("gender", "sex", "description", "name", "label")).lower()
    # "Nam" alone also names the southern accent ("giọng Nam"), so prefer explicit fields.
    gender = str(item.get("gender") or item.get("sex") or "").strip().lower()
    if gender in ("male", "m", "nam"):
        return "male"
    if gender in ("female", "f", "nữ", "nu"):
        return "female"
    if "female" in text or "nữ" in text:
        return "female"
    if "male" in text or "nam giới" in text or "giọng nam" in text:
        return "male"
    return "female"


def _parse_cit_voices(payload: object) -> list[CITVoice]:
    if isinstance(payload, dict):
        payload = payload.get("voices") or payload.get("data") or []
    voices: list[CITVoice] = []
    seen: set[str] = set()
    for item in payload if isinstance(payload, list) else []:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            continue
        voice_id = str(item.get("voice_id") or item.get("id") or item.get("name") or "").strip()
        if not voice_id or voice_id in seen:
            continue
        seen.add(voice_id)
        label = str(item.get("label") or item.get("name") or voice_id).strip()
        description = str(item.get("description") or "").strip()
        if description and description.lower() not in label.lower():
            label = f"{label} ({description})"
        voices.append(CITVoice(voice_id, label[:120], _cit_voice_gender(item)))
    return voices


def cit_voices() -> list[CITVoice]:
    """Voices offered by the running CIT Voice Studio app (built-in and cloned)."""

    if not cit_base_url():
        return []
    now = time.monotonic()
    with _cit_cache_lock:
        cached = _cit_cache.get("voices")
    if cached is not None and now - cached[0] < CIT_STATUS_TTL_SECONDS:
        return list(cached[1])
    try:
        with urlopen(_cit_request("/voices"), timeout=CIT_VOICES_TIMEOUT_SECONDS) as response:
            voices = _parse_cit_voices(json.loads(response.read(2 * 1024 * 1024)))
    except (HTTPError, URLError, OSError, ValueError) as exc:
        busy = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
        if cached is not None and cached[1] and busy:
            # Remember "busy" for a TTL so the next page loads do not each wait for the timeout.
            with _cit_cache_lock:
                _cit_cache["voices"] = (now, cached[1])
            return list(cached[1])
        if cached is not None and cached[1] and now - cached[0] < CIT_STALE_SECONDS:
            return list(cached[1])
        logger.warning("CIT Voice Studio voice list failed: %s", type(exc).__name__)
        voices = []
    with _cit_cache_lock:
        _cit_cache["voices"] = (now, voices)
    return list(voices)


def _cit_status() -> TTSProviderStatus:
    voices = cit_voices()
    female = _default_cit_voice(voices, "female")
    male = _default_cit_voice(voices, "male")
    return TTSProviderStatus(
        "cit",
        "CIT Voice Studio",
        bool(voices),
        "Chạy local trên máy chủ (VieNeu-TTS), miễn phí, không giới hạn ký tự",
        female.label if female else "Nữ — CIT Voice Studio",
        male.label if male else "Nam — CIT Voice Studio",
        False,
        [voice.public() for voice in voices],
    )


def _default_cit_voice(voices: list[CITVoice], gender: str) -> CITVoice | None:
    preferred = os.environ.get(f"VIDTRANS_CIT_VOICE_{gender.upper()}", "").strip()
    by_key = {voice.key: voice for voice in voices}
    if preferred in by_key:
        return by_key[preferred]
    return next((voice for voice in voices if voice.gender == gender), voices[0] if voices else None)


# CIT labels read "Name (Nữ · Bắc · Phong cách kể chuyện · 48 kHz)". Narrative
# styles are the warmest for dubbing; news reads are the stiffest.
_CIT_STYLE_SOFTNESS = (("kể chuyện", 0), ("đọc truyện", 0), ("tự nhiên", 1), ("tin tức", 3))
_NOVELTY_VOICE_MARKERS = ("bựa", "hài", "troll")


def _cit_voice_softness(label: str) -> int:
    lowered = label.lower()
    return next((rank for marker, rank in _CIT_STYLE_SOFTNESS if marker in lowered), 2)


def _cit_voice_region(label: str) -> str | None:
    parts = [part.strip().lower() for part in label.partition("(")[2].rstrip(")").split("·")]
    return parts[1] if len(parts) > 1 else None


def _is_novelty_voice(label: str) -> bool:
    name = label.partition("(")[0].lower()
    return any(re.search(rf"(?<!\w){marker}(?!\w)", name) for marker in _NOVELTY_VOICE_MARKERS)


def tts_voice_pool(provider: str, selected: dict[str, str] | None) -> dict[str, list[str]]:
    """Voices per style for multi-character dubbing: the selection first, then the
    provider's other voices of the same style. Empty when the provider has one voice."""

    provider = normalize_tts_provider(provider)
    if provider == "cit":
        voices = cit_voices()
        regions = {voice.key: _cit_voice_region(voice.label) for voice in voices}
        primary_regions = {regions.get(key) for key in (selected or {}).values() if key}
        # Extra characters get the softest voices first, in the narrator's accent.
        ranked = sorted(
            (voice for voice in voices if not _is_novelty_voice(voice.label)),
            key=lambda voice: (_cit_voice_softness(voice.label), regions[voice.key] not in primary_regions),
        )
        available = [(voice.key, voice.gender) for voice in ranked]
    elif provider == "elevenlabs":
        available = [(voice.key, voice.gender) for voice in elevenlabs_voices()]
    else:
        return {}
    selected = selected or {}
    # A voice the user picked for the other style is never reused as an extra
    # character, or the narrator of one style would speak for the other too.
    reserved = {key for key in selected.values() if key}
    pool: dict[str, list[str]] = {}
    for gender in ("female", "male"):
        primary = selected.get(gender)
        voices = [primary] if primary else []
        voices += [key for key, voice_gender in available if voice_gender == gender and key not in reserved]
        if voices:
            pool[gender] = voices
    return pool


def resolve_tts_voice_selection(
    provider: str, female_key: str | None = None, male_key: str | None = None
) -> dict[str, str]:
    """Validate a form's voice choice for providers with selectable voices."""

    provider = normalize_tts_provider(provider)
    if provider == "elevenlabs":
        return resolve_elevenlabs_voice_selection(female_key, male_key)
    if provider != "cit":
        return {}
    voices = cit_voices()
    by_key = {voice.key: voice for voice in voices}
    selection: dict[str, str] = {}
    for gender, key in (("female", female_key), ("male", male_key)):
        key = (key or "").strip()
        if key and key not in by_key:
            raise ValueError(f"Giọng CIT Voice Studio đã chọn không còn trong ứng dụng: {key}")
        voice = by_key.get(key) or _default_cit_voice(voices, gender)
        if voice is not None:
            selection[gender] = voice.key
    return selection


def _cit_slot() -> threading.BoundedSemaphore:
    global _cit_semaphore
    with _cit_semaphore_guard:
        if _cit_semaphore is None:
            try:
                limit = max(1, int(os.environ.get("VIDTRANS_CIT_VOICE_CONCURRENCY", "1")))
            except ValueError:
                limit = 1
            _cit_semaphore = threading.BoundedSemaphore(limit)
        return _cit_semaphore


def cit_can_voice(text: str) -> bool:
    """VieNeu only reads Latin script and digits.

    A cue left in Chinese, Japanese or Cyrillic (an untranslated ASR hallucination) makes
    the app answer HTTP 500 "Không tạo được dữ liệu âm thanh"; mixed lines are fine.
    """

    return any(
        character.isdigit() or (character.isalpha() and unicodedata.name(character, "").startswith("LATIN"))
        for character in text or ""
    )


def synthesize_cit(
    text: str,
    output_path: Path,
    *,
    voice_type: str = "female",
    voice_key: str | None = None,
    speed: float = 1.0,
    language: str = "vi",
) -> None:
    if not cit_base_url():
        ensure_provider_configured("cit")
    voice_id = (voice_key or "").strip()
    if not voice_id:
        voice = _default_cit_voice(cit_voices(), "male" if voice_type == "male" else "female")
        if voice is None:
            raise TTSProviderUnavailable(f"Không kết nối được CIT Voice Studio tại {cit_base_url()}")
        voice_id = voice.key
    payload = {
        "text": text,
        "voice_id": voice_id,
        "speed": round(max(CIT_SPEED_RANGE[0], min(CIT_SPEED_RANGE[1], speed)), 3),
        # Be explicit: CIT otherwise derives the format from UI state in some releases.
        # Every downstream VidTrans step accepts WAV and the cue files use a .wav suffix.
        "format": "wav",
        # VieNeu ignores it; the optional Supertonic model needs a bare ISO code.
        "language": (language or "vi").split("-")[0].lower(),
    }
    # The model runs on the server's CPU: parallel cues would only thrash it.
    with _cit_slot():
        for attempt in range(1, CIT_GENERATE_ATTEMPTS + 1):
            # Request objects are rebuilt because urllib requests with a body are not
            # guaranteed to be reusable after an HTTPError.
            request = _cit_request("/api/tts/generate", payload)
            try:
                with urlopen(request, timeout=CIT_GENERATE_TIMEOUT_SECONDS) as response:
                    audio = response.read()
                    content_type = response.headers.get_content_type() or ""
                break
            except HTTPError as exc:
                detail = exc.read(400).decode("utf-8", errors="replace")
                # CIT can briefly answer 500 while its local model is loading or being
                # switched. Retry once; validation/authentication failures must surface
                # immediately so a bad request is never repeated.
                if exc.code < 500 or attempt == CIT_GENERATE_ATTEMPTS:
                    raise TTSProviderHTTPError("CIT Voice Studio", exc.code, detail) from exc
                logger.warning(
                    "CIT Voice Studio generation failed with HTTP %s; retrying once",
                    exc.code,
                )
                time.sleep(CIT_RETRY_BASE_SECONDS)
            except (URLError, OSError) as exc:
                with _cit_cache_lock:
                    _cit_cache.clear()
                reason = getattr(exc, "reason", exc)
                raise TTSProviderUnavailable(f"Không kết nối được CIT Voice Studio: {reason}") from exc
    if not audio or content_type.startswith(("application/json", "text/")):
        raise RuntimeError("CIT Voice Studio không trả về dữ liệu âm thanh")
    output_path.write_bytes(audio)


def audio_content_type(audio: bytes) -> str:
    if audio.startswith(b"RIFF"):
        return "audio/wav"
    if audio.startswith(b"OggS"):
        return "audio/ogg"
    if audio.startswith(b"fLaC"):
        return "audio/flac"
    return "audio/mpeg"


def cit_voice_preview(voice_key: str) -> tuple[bytes, str]:
    """Synthesize the preview sentence with a CIT Voice Studio voice.

    Generation runs on the server CPU, so each voice's clip is cached in memory.
    """

    ensure_provider_configured("cit")
    if voice_key not in {voice.key for voice in cit_voices()}:
        raise LookupError("Giọng CIT Voice Studio không còn trong ứng dụng")
    with _cit_previews_lock:
        cached = _cit_previews.get(voice_key)
    if cached is not None:
        return cached

    # A double check inside the per-voice lock prevents rapid clicks (or multiple
    # browser tabs) from making the CPU synthesize the same clip concurrently.
    with _cit_preview_lock(voice_key):
        with _cit_previews_lock:
            cached = _cit_previews.get(voice_key)
        if cached is not None:
            return cached

        cache_path = _cit_preview_cache_path(voice_key)
        try:
            audio = cache_path.read_bytes()
        except (FileNotFoundError, OSError):
            audio = b""
        if not audio:
            with tempfile.TemporaryDirectory(prefix="vidtrans-preview-") as directory:
                output_path = Path(directory) / "preview.audio"
                synthesize_cit(VOICE_PREVIEW_TEXT, output_path, voice_key=voice_key)
                audio = output_path.read_bytes()
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = cache_path.with_suffix(f".{os.getpid()}.tmp")
                temporary.write_bytes(audio)
                temporary.replace(cache_path)
            except OSError:
                # Disk cache is an optimisation; a read-only/misconfigured cache
                # directory must not make an otherwise valid preview fail.
                logger.warning("Cannot persist CIT voice preview cache at %s", cache_path)

        result = (audio, audio_content_type(audio))
        with _cit_previews_lock:
            _cit_previews[voice_key] = result
        return result


def warm_cit_voice_previews() -> tuple[int, int]:
    """Create persistent preview clips for every currently available CIT voice.

    Intended for a background startup task. Existing disk clips return immediately,
    while missing clips are generated sequentially through CIT's normal CPU guard.
    """

    voices = cit_voices()
    ready = 0
    failed = 0
    for voice in voices:
        try:
            cit_voice_preview(voice.key)
            ready += 1
        except Exception as exc:
            failed += 1
            logger.warning("Cannot prepare CIT preview for voice %s: %s", voice.key, exc)
    return ready, failed
