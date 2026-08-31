import asyncio
import ipaddress
import json
import logging
import os
import shutil
import subprocess
import textwrap
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote_plus

import whisper
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from gtts import gTTS

from app.config import AppSettings
from application.job_scheduler import JobScheduler
from application.job_service import JobService
from domain.models import ProcessingMode, ProcessingRequest
from infrastructure.douyin_qr_auth import DouyinQRAuthManager
from infrastructure.auth import (
    AuthConfigurationError,
    AuthManager,
    InvalidTokenError,
    LoginRateLimitError,
)
from infrastructure.job_store import SQLiteJobStore
from infrastructure.media_validation import InvalidVideoError, public_error_message, validate_video_file
from infrastructure.social_video_downloader import (
    SocialVideoDownloadCancelled,
    SocialVideoDownloader,
    SocialVideoDownloadError,
    extract_social_video_urls,
    social_platform,
)
from infrastructure.tiktok_publisher import TikTokPublisher, TikTokPublisherError
from pipeline.ocr import OCRConfig, annotate_ocr_segments_with_asr, extract_burned_subtitle_segments
from pipeline.subtitle_layout import SubtitleLayoutOptions, apply_subtitle_layout
from pipeline.tiktok import LocalExtractiveTikTokProvider, write_tiktok_artifacts
from pipeline.translation import translate_segments
from pipeline.voice_routing import route_segments_by_pitch, route_segments_manually

try:
    import edge_tts
except Exception:  # pragma: no cover
    edge_tts = None


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

SETTINGS = AppSettings.load(Path(__file__).resolve().parent)
BASE_DIR = SETTINGS.base_dir
FRONTEND_DIR = SETTINGS.frontend_dir
UPLOAD_DIR = SETTINGS.upload_dir
OUTPUT_DIR = SETTINGS.output_dir
WORK_DIR = SETTINGS.work_dir
FFMPEG = SETTINGS.ffmpeg
FFPROBE = SETTINGS.ffprobe

VOICE_OPTIONS = {
    "female": "vi-VN-HoaiMyNeural",
    "male": "vi-VN-NamMinhNeural",
}
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
DEFAULT_SUB_STYLE = {
    "font_name": "Noto Sans CJK SC",
    "font_size": 36,
    "primary_color": "&H00FFFFFF",
    "outline_color": "&H00000000",
    "back_color": "&H66000000",
    "outline": 3,
    "shadow": 0,
    "alignment": 2,
    "margin_v": 60,
    "placement_mode": "replace_original",
    "match_source_size": True,
    "min_font_size": 22,
    "max_font_size": 72,
    "position_gap": 14,
    "mask_original": True,
}
FONT_CANDIDATES = [
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "DejaVu Sans",
    "Arial",
    "Helvetica",
    "Arial Unicode MS",
]
FONT_DIR = BASE_DIR / "assets" / "fonts"
BUNDLED_FONT = FONT_DIR / "NotoSansCJKsc-Regular.otf"

app = FastAPI(title="VidTrans", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

JOB_SERVICE = JobService(SQLiteJobStore(WORK_DIR / "jobs.sqlite3"))
TIKTOK_SUMMARY_PROVIDER = LocalExtractiveTikTokProvider()
_social_cookie_value = os.environ.get("VIDTRANS_YTDLP_COOKIE_FILE", "").strip()
SOCIAL_VIDEO_DOWNLOADER = SocialVideoDownloader(
    ffmpeg_location=FFMPEG,
    cookie_file=Path(_social_cookie_value) if _social_cookie_value else None,
)
DOUYIN_QR_AUTH = DouyinQRAuthManager(WORK_DIR / "douyin-auth")
TIKTOK_PUBLISHER = TikTokPublisher(WORK_DIR / "tiktok-auth")
AUTH_MANAGER = AuthManager()
_whisper_models: dict[str, Any] = {}
_whisper_slots = threading.BoundedSemaphore(SETTINGS.whisper_concurrency)
_ocr_slots = threading.BoundedSemaphore(SETTINGS.ocr_concurrency)
_job_context = threading.local()


class JobCancelled(RuntimeError):
    pass


PUBLIC_AUTH_PATHS = {
    "/api/v1/auth/status",
    "/api/v1/auth/token",
    "/api/v1/tiktok-auth/callback",
}
SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_protected_path(path: str) -> bool:
    return (
        path.startswith("/api/")
        or path.startswith("/process-video")
        or path.startswith("/convert")
        or path.startswith("/status/")
        or path.startswith("/download/")
        or path in {"/docs", "/redoc", "/openapi.json"}
    ) and path not in PUBLIC_AUTH_PATHS


def request_access_token(request: Request) -> tuple[str | None, bool, bool]:
    authorization = request.headers.get("authorization", "").strip()
    if authorization:
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            return None, False, True
        return token.strip(), False, False
    cookie_token = request.cookies.get(AUTH_MANAGER.config.cookie_name)
    return cookie_token, bool(cookie_token), False


def auth_client_id(request: Request) -> str:
    if os.environ.get("VIDTRANS_TRUST_PROXY_HEADERS", "0").strip().lower() in {"1", "true", "yes", "on"}:
        forwarded = request.headers.get("x-real-ip", "").strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def unauthorized_response(detail: str = "Phiên đăng nhập không hợp lệ hoặc đã hết hạn") -> JSONResponse:
    return JSONResponse(
        {"detail": detail},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer scope="admin"', "Cache-Control": "no-store"},
    )


@app.middleware("http")
async def authenticate_request(request: Request, call_next: Callable[..., Any]):
    if not AUTH_MANAGER.config.enabled or not is_protected_path(request.url.path):
        return await call_next(request)
    if not AUTH_MANAGER.config.configured:
        return JSONResponse(
            {"detail": "Xác thực đã bật nhưng cấu hình máy chủ chưa hợp lệ"},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    token, from_cookie, malformed_header = request_access_token(request)
    if malformed_header or not token:
        return unauthorized_response()
    try:
        request.state.auth_claims = AUTH_MANAGER.verify_token(token)
    except (AuthConfigurationError, InvalidTokenError):
        return unauthorized_response()
    if (
        from_cookie
        and request.method.upper() not in SAFE_HTTP_METHODS
        and request.headers.get("x-vidtrans-request") != "1"
    ):
        return JSONResponse(
            {"detail": "Thiếu tiêu đề chống CSRF"},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ff_escape_path(path: Path) -> str:
    escaped = str(path).replace("\\", "/")
    for src, dest in {
        ":": "\\:",
        "'": "\\'",
        ",": "\\,",
        "[": "\\[",
        "]": "\\]",
    }.items():
        escaped = escaped.replace(src, dest)
    return escaped


def ff_escape_text(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for src, dest in {
        ":": "\\:",
        "'": "\\'",
        ",": "\\,",
        "[": "\\[",
        "]": "\\]",
    }.items():
        escaped = escaped.replace(src, dest)
    return escaped


def run_ffmpeg(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    logger.info("Running ffmpeg command: %s", " ".join(args))
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            job_id = getattr(_job_context, "job_id", None)
            if job_id and JOB_SERVICE.is_cancel_requested(job_id):
                process.terminate()
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise JobCancelled("FFmpeg đã dừng theo yêu cầu hủy")
    result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    if check and process.returncode:
        exc = subprocess.CalledProcessError(process.returncode, args, stdout, stderr)
        logger.error("ffmpeg failed with stderr:\n%s", stderr)
        raise exc
    return result


def run_cmd(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    logger.info("Running command: %s", " ".join(args))
    return subprocess.run(args, check=check, capture_output=True, text=True)


def format_time(seconds: float, for_ass: bool = False) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        secs += 1
        millis = 0
    if for_ass:
        centis = millis // 10
        return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def wrap_text(text: str, width: int = 34) -> str:
    words = " ".join(text.split()).split()
    lines: list[str] = []
    current = ""
    for word in words:
        extra = len(word) + (1 if current else 0)
        if len(current) + extra <= width:
            current += (" " if current else "") + word
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines[:2] if len(lines) > 2 else lines)


def find_font_file(preferred_name: str) -> Optional[str]:
    if BUNDLED_FONT.is_file():
        return str(BUNDLED_FONT)
    fc_match = shutil.which("fc-match")
    if not fc_match:
        return None
    for candidate in [preferred_name, *FONT_CANDIDATES]:
        try:
            result = run_cmd([fc_match, "-f", "%{file}", candidate], check=False)
            font_file = (result.stdout or "").strip()
            if font_file and Path(font_file).exists():
                return font_file
        except Exception:
            continue
    return None


def write_srt(segments: list[dict[str, Any]], output_path: Path) -> None:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        text = wrap_text(segment["text"])
        if not text:
            continue
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_time(segment['start'])} --> {format_time(segment['end'])}",
                    text,
                ]
            )
        )
    output_path.write_text("\n\n".join(blocks), encoding="utf-8")


def write_ass(
    segments: list[dict[str, Any]],
    output_path: Path,
    style: dict[str, Any],
    *,
    video_width: int = 1280,
    video_height: int = 720,
) -> None:
    style_line = (
        "Style: Default,{font_name},{font_size},{primary_color},{primary_color},"
        "{outline_color},{back_color},0,0,0,0,100,100,0,0,1,{outline},{shadow},"
        "{alignment},10,10,{margin_v},1"
    ).format(**style)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {video_width}",
        f"PlayResY: {video_height}",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding",
        style_line,
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for segment in segments:
        layout = segment.get("subtitle_layout") if isinstance(segment.get("subtitle_layout"), dict) else {}
        raw_text = str(layout.get("render_text") or wrap_text(segment["text"]))
        text = raw_text.replace("\n", "\\N")
        text = text.replace("{", "\\{").replace("}", "\\}")
        if layout.get("mask_original") and isinstance(layout.get("mask"), dict):
            mask = layout["mask"]
            mask_x = int(round(float(mask["x"])))
            mask_y = int(round(float(mask["y"])))
            mask_width = max(1, int(round(float(mask["width"]))))
            mask_height = max(1, int(round(float(mask["height"]))))
            lines.append(
                "Dialogue: 0,{start},{end},Default,,0,0,0,,"
                "{{\\an7\\pos({x},{y})\\p1\\bord0\\shad0\\1c&H000000&\\1a&H45&}}"
                "m 0 0 l {width} 0 l {width} {height} l 0 {height}".format(
                    start=format_time(segment["start"], for_ass=True),
                    end=format_time(segment["end"], for_ass=True),
                    x=mask_x,
                    y=mask_y,
                    width=mask_width,
                    height=mask_height,
                )
            )
        override = ""
        if layout:
            override = "{{\\an5\\pos({x},{y})\\fs{font_size}}}".format(
                x=int(round(float(layout["x"]))),
                y=int(round(float(layout["y"]))),
                font_size=int(layout["font_size"]),
            )
        lines.append(
            "Dialogue: 1,{start},{end},Default,,0,0,0,,{override}{text}".format(
                start=format_time(segment["start"], for_ass=True),
                end=format_time(segment["end"], for_ass=True),
                override=override,
                text=text,
            )
        )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def get_media_duration(path: Path) -> float:
    result = run_cmd(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return float((result.stdout or "0").strip() or 0)


def get_video_dimensions(path: Path) -> tuple[int, int]:
    result = run_cmd(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(path),
        ]
    )
    raw = (result.stdout or "").strip().lower()
    if "x" not in raw:
        raise RuntimeError("Không đọc được độ phân giải video")
    width, height = raw.split("x", 1)
    return int(width), int(height)


def get_video_fps(path: Path) -> float:
    result = run_cmd(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    raw = (result.stdout or "").strip()
    if "/" in raw:
        num, denom = raw.split("/", 1)
        if float(denom) != 0:
            return float(num) / float(denom)
    try:
        return float(raw)
    except Exception:
        return 30.0


def get_whisper_model(model_name: str) -> Any:
    if model_name not in _whisper_models:
        logger.info("Loading whisper model: %s", model_name)
        _whisper_models[model_name] = whisper.load_model(model_name)
    return _whisper_models[model_name]


def transcribe_chinese_video(model: Any, video_path: Path) -> list[dict[str, Any]]:
    result = model.transcribe(
        str(video_path),
        language="zh",
        fp16=False,
        temperature=0,
        best_of=5,
        beam_size=5,
        condition_on_previous_text=True,
        word_timestamps=True,
        hallucination_silence_threshold=1.0,
        verbose=False,
    )
    segments = normalize_segments(result.get("segments", []))
    if segments:
        return segments

    logger.warning("No transcript segments with forced zh, retrying with simpler whisper settings")
    result = model.transcribe(
        str(video_path),
        language="zh",
        fp16=False,
        word_timestamps=True,
        verbose=False,
    )
    segments = normalize_segments(result.get("segments", []))
    if segments:
        return segments

    logger.warning("No transcript segments with forced zh, retrying with auto language detection")
    result = model.transcribe(
        str(video_path),
        fp16=False,
        word_timestamps=True,
        verbose=False,
    )
    return normalize_segments(result.get("segments", []))


def update_job(job_id: str, **fields: Any) -> None:
    current = JOB_SERVICE.get(job_id)
    if current and current.get("cancel_requested"):
        if fields.get("status") == "processing":
            fields.pop("status")
        elif fields.get("status") == "completed":
            raise JobCancelled("Job đã được yêu cầu hủy trước khi hoàn tất")
    JOB_SERVICE.update(job_id, **fields)


def ensure_job_active(job_id: str) -> None:
    if JOB_SERVICE.is_cancel_requested(job_id):
        raise JobCancelled("Job đã được người dùng yêu cầu hủy")


def make_ocr_progress_callback(job_id: str) -> Callable[[int, int], None]:
    """Persist at most about 100 OCR progress updates for one job."""

    last_reported = 0

    def report(completed: int, total: int) -> None:
        nonlocal last_reported
        report_interval = max(1, total // 100)
        if completed != total and completed - last_reported < report_interval:
            return
        last_reported = completed
        update_job(
            job_id,
            progress=0.12 + (0.08 * completed / max(total, 1)),
            step_detail=f"OCR {completed}/{total} frames",
        )

    return report


async def tts_edge_sync(text: str, output_path: Path, voice_name: str, rate: str) -> None:
    if edge_tts is None:
        raise RuntimeError("edge-tts is not available")
    communicate = edge_tts.Communicate(text=text, voice=voice_name, rate=rate)
    await communicate.save(str(output_path))


def tts_gtts_sync(text: str, output_path: Path, slow: bool = False) -> None:
    gTTS(text=text, lang="vi", slow=slow).save(str(output_path))


def synthesize_tts_segments(
    segments: list[dict[str, Any]],
    work_dir: Path,
    voice_type: str,
    speech_rate: float,
    job_id: str | None = None,
) -> list[Path]:
    tts_dir = work_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    rate_percent = int((speech_rate - 1.0) * 100)
    rate_string = f"{rate_percent:+d}%"
    paths: list[Path] = []
    for index, segment in enumerate(segments):
        if job_id:
            ensure_job_active(job_id)
        text = " ".join(segment["text"].split())
        if not text:
            continue
        output_path = tts_dir / f"{index:04d}.mp3"
        selected_voice_type = str(segment.get("voice_type", voice_type))
        voice_name = VOICE_OPTIONS.get(selected_voice_type, VOICE_OPTIONS["female"])
        try:
            asyncio.run(tts_edge_sync(text, output_path, voice_name, rate_string))
        except Exception as exc:
            logger.warning("edge-tts failed, fallback to gTTS: %s", exc)
            tts_gtts_sync(text, output_path, slow=speech_rate < 0.95)
        cue_duration = max(0.25, float(segment["end"]) - float(segment["start"]) - 0.05)
        rendered_duration = get_media_duration(output_path)
        fitted_path = output_path
        if rendered_duration > cue_duration * 1.05:
            speed_factor = rendered_duration / cue_duration
            fitted_path = tts_dir / f"{index:04d}_fitted.wav"
            run_ffmpeg(
                [
                    FFMPEG,
                    "-y",
                    "-i",
                    str(output_path),
                    "-af",
                    build_atempo_filter(speed_factor),
                    "-t",
                    f"{cue_duration:.3f}",
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    str(fitted_path),
                ]
            )
            segment["tts_speed_factor"] = round(speed_factor, 3)
        paths.append(fitted_path)
        segment["tts_path"] = fitted_path
        if job_id and (index == len(segments) - 1 or index % max(1, len(segments) // 30) == 0):
            update_job(
                job_id,
                progress=0.55 + (0.15 * (index + 1) / max(len(segments), 1)),
                step_detail=f"Đã tạo giọng đọc {index + 1}/{len(segments)} câu",
            )
    return paths


def build_atempo_filter(speed_factor: float) -> str:
    """Build an FFmpeg atempo chain while keeping every factor in [0.5, 2]."""
    factor = max(0.5, float(speed_factor))
    parts: list[float] = []
    while factor > 2.0:
        parts.append(2.0)
        factor /= 2.0
    while factor < 0.5:
        parts.append(0.5)
        factor /= 0.5
    parts.append(factor)
    return ",".join(f"atempo={part:.5f}" for part in parts)


def build_voice_track(segments: list[dict[str, Any]], work_dir: Path, video_duration: float) -> Path:
    voice_track = work_dir / "voice_track.wav"
    input_args: list[str] = []
    filter_parts = []
    labels = []
    for index, segment in enumerate(segments):
        tts_path = segment.get("tts_path")
        if not tts_path:
            continue
        delay_ms = max(0, int(segment["start"] * 1000))
        input_args.extend(["-i", str(tts_path)])
        filter_parts.append(f"[{index}:a]adelay={delay_ms}|{delay_ms},volume=1.4[a{index}]")
        labels.append(f"[a{index}]")
    if not labels:
        raise RuntimeError("No TTS segments were generated")
    filter_parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,alimiter=limit=0.95[out]")
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            *input_args,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[out]",
            "-t",
            f"{video_duration:.3f}",
            str(voice_track),
        ]
    )
    return voice_track


def extract_original_audio(video_path: Path, output_path: Path) -> Path:
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            str(output_path),
        ]
    )
    return output_path


def has_audio_stream(video_path: Path) -> bool:
    result = run_cmd(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(video_path),
        ],
        check=False,
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def extract_voice_analysis_audio(video_path: Path, output_path: Path) -> Path:
    """Create the compact mono PCM input used by the local pitch router."""
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    return output_path


def mix_audio(
    *,
    mode: int,
    video_path: Path,
    work_dir: Path,
    voice_track: Optional[Path],
    background_music: Optional[Path],
    keep_original_audio: bool,
    original_audio_volume: float,
    music_volume: float,
) -> Optional[Path]:
    if mode == 1:
        return None

    output_audio = work_dir / "mixed_audio.wav"
    input_args: list[str] = []
    filter_parts: list[str] = []
    labels: list[str] = []
    input_index = 0

    if voice_track:
        input_args.extend(["-i", str(voice_track)])
        filter_parts.append(f"[{input_index}:a]volume=1.0,alimiter=limit=0.95[v]")
        labels.append("[v]")
        input_index += 1

    if keep_original_audio and has_audio_stream(video_path):
        original_audio = extract_original_audio(video_path, work_dir / "original_audio.wav")
        input_args.extend(["-i", str(original_audio)])
        filter_parts.append(f"[{input_index}:a]volume={original_audio_volume:.2f}[o]")
        labels.append("[o]")
        input_index += 1
    elif keep_original_audio:
        logger.info("Source video has no audio stream; continuing with generated audio only")

    if background_music:
        duration = get_media_duration(video_path)
        looped_music = work_dir / "looped_music.wav"
        run_ffmpeg(
            [
                FFMPEG,
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                str(background_music),
                "-t",
                f"{duration:.3f}",
                "-af",
                f"volume={music_volume:.2f},afade=t=in:st=0:d=1.5,afade=t=out:st={max(duration - 2.0, 0):.3f}:d=2",
                str(looped_music),
            ]
        )
        input_args.extend(["-i", str(looped_music)])
        filter_parts.append(f"[{input_index}:a]volume=1.0[m]")
        labels.append("[m]")
        input_index += 1

    if not labels:
        return None

    filter_parts.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,loudnorm=I=-16:LRA=11:TP=-1.5[out]"
    )
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            *input_args,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[out]",
            str(output_audio),
        ]
    )
    return output_audio


def build_drawtext_filter(segments: list[dict[str, Any]], subtitle_style: dict[str, Any]) -> str:
    font_file = find_font_file(subtitle_style.get("font_name", "Arial"))
    font_size = subtitle_style.get("font_size", 22)
    margin_v = subtitle_style.get("margin_v", 30)
    filters = []
    for segment in segments:
        layout = segment.get("subtitle_layout") if isinstance(segment.get("subtitle_layout"), dict) else {}
        text = str(layout.get("render_text") or wrap_text(segment["text"]))
        if not text:
            continue
        escaped_text = ff_escape_text(text).replace("\n", "\\n")
        active_font_size = int(layout.get("font_size", font_size))
        x_expression = str(int(round(float(layout["x"])))) + "-text_w/2" if layout else "(w-text_w)/2"
        y_expression = str(int(round(float(layout["y"])))) + "-text_h/2" if layout else f"h-text_h-{margin_v}"
        parts = [
            f"text='{escaped_text}'",
            f"fontsize={active_font_size}",
            "fontcolor=white",
            "line_spacing=6",
            f"x={x_expression}",
            f"y={y_expression}",
            "box=1",
            "boxcolor=black@0.45",
            "boxborderw=12",
            f"enable='between(t,{segment['start']:.3f},{segment['end']:.3f})'",
        ]
        if font_file:
            parts.insert(0, f"fontfile='{ff_escape_path(Path(font_file))}'")
        filters.append(f"drawtext={':'.join(parts)}")
    return ",".join(filters)


def _load_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Pillow is required for subtitle rendering fallback. Please run `pip install -r requirements.txt`."
        ) from exc
    return Image, ImageDraw, ImageFont


def burn_subtitles_with_pillow(
    video_path: Path,
    output_path: Path,
    audio_path: Optional[Path],
    segments: list[dict[str, Any]],
    subtitle_style: dict[str, Any],
    work_dir: Path,
) -> None:
    Image, ImageDraw, ImageFont = _load_pillow()
    frames_dir = work_dir / "frames"
    rendered_dir = work_dir / "rendered_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    rendered_dir.mkdir(parents=True, exist_ok=True)

    fps = get_video_fps(video_path)
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            "-i",
            str(video_path),
            "-vsync",
            "0",
            str(frames_dir / "frame_%06d.png"),
        ]
    )

    font_size = int(subtitle_style.get("font_size", 22))
    margin_v = int(subtitle_style.get("margin_v", 30))
    font_path = find_font_file(subtitle_style.get("font_name", "Arial"))
    frame_paths = sorted(frames_dir.glob("frame_*.png"))

    for index, frame_path in enumerate(frame_paths):
        timestamp = index / fps
        active = next((seg for seg in segments if seg["start"] <= timestamp <= seg["end"]), None)
        output_frame = rendered_dir / frame_path.name
        if not active:
            shutil.copy2(frame_path, output_frame)
            continue

        image = Image.open(frame_path).convert("RGBA")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        layout = active.get("subtitle_layout") if isinstance(active.get("subtitle_layout"), dict) else {}
        active_font_size = int(layout.get("font_size", font_size))
        font = ImageFont.truetype(font_path, active_font_size) if font_path else ImageFont.load_default()
        text = str(layout.get("render_text") or wrap_text(active["text"]))
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=6, align="center")
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        if layout:
            x = float(layout["x"]) - text_w / 2
            y = float(layout["y"]) - text_h / 2
        else:
            x = (image.width - text_w) / 2
            y = image.height - text_h - margin_v
        pad_x = 20
        pad_y = 12
        if layout.get("mask_original") and isinstance(layout.get("mask"), dict):
            mask = layout["mask"]
            draw.rounded_rectangle(
                (
                    float(mask["x"]),
                    float(mask["y"]),
                    float(mask["x"]) + float(mask["width"]),
                    float(mask["y"]) + float(mask["height"]),
                ),
                radius=max(4, active_font_size // 4),
                fill=(0, 0, 0, 150),
            )
        else:
            draw.rounded_rectangle(
                (x - pad_x, y - pad_y, x + text_w + pad_x, y + text_h + pad_y),
                radius=18,
                fill=(0, 0, 0, 140),
            )
        draw.multiline_text((x, y), text, font=font, fill=(255, 255, 255, 255), spacing=6, align="center")
        combined = Image.alpha_composite(image, overlay).convert("RGB")
        combined.save(output_frame)

    rendered_video = work_dir / "rendered_video.mp4"
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            "-framerate",
            f"{fps:.6f}",
            "-i",
            str(rendered_dir / "frame_%06d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            str(rendered_video),
        ]
    )

    mux_source = audio_path or video_path
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            "-i",
            str(rendered_video),
            "-i",
            str(mux_source),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ]
    )


def burn_subtitles(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    audio_path: Optional[Path],
    segments: list[dict[str, Any]],
    subtitle_style: dict[str, Any],
    work_dir: Path,
) -> None:
    subtitle_filter = f"subtitles=filename='{ff_escape_path(subtitle_path)}'"
    if BUNDLED_FONT.is_file():
        subtitle_filter += (
            f":fontsdir='{ff_escape_path(FONT_DIR)}'"
            ":force_style='FontName=Noto Sans CJK SC'"
        )
    cmd = [
        FFMPEG,
        "-y",
        "-i",
        str(video_path),
    ]
    if audio_path:
        cmd.extend(["-i", str(audio_path)])
    cmd.extend(
        [
            "-vf",
            subtitle_filter,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
        ]
    )
    if audio_path:
        cmd.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-shortest"])
    else:
        cmd.extend(["-c:a", "copy"])
    cmd.append(str(output_path))
    try:
        run_ffmpeg(cmd)
    except subprocess.CalledProcessError as exc:
        if "No such filter: 'subtitles'" not in (exc.stderr or ""):
            raise
        logger.warning("ffmpeg subtitles filter is unavailable, falling back to drawtext rendering")
        fallback_filter = build_drawtext_filter(segments, subtitle_style)
        fallback_cmd = [
            FFMPEG,
            "-y",
            "-i",
            str(video_path),
        ]
        if audio_path:
            fallback_cmd.extend(["-i", str(audio_path)])
        fallback_cmd.extend(
            [
                "-vf",
                fallback_filter,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
            ]
        )
        if audio_path:
            fallback_cmd.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-shortest"])
        else:
            fallback_cmd.extend(["-c:a", "copy"])
        fallback_cmd.append(str(output_path))
        try:
            run_ffmpeg(fallback_cmd)
        except subprocess.CalledProcessError as fallback_exc:
            if "No such filter: 'drawtext'" not in (fallback_exc.stderr or ""):
                raise
            logger.warning("ffmpeg drawtext filter is unavailable, falling back to Pillow frame rendering")
            burn_subtitles_with_pillow(video_path, output_path, audio_path, segments, subtitle_style, work_dir)


def normalize_segments(raw_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for segment in raw_segments:
        text = " ".join((segment.get("text") or "").split())
        if not text:
            continue
        start = max(0.0, float(segment.get("start", 0)))
        end = max(start + 0.1, float(segment.get("end", start + 0.1)))
        words = []
        for word in segment.get("words") or []:
            word_text = (word.get("word") or "").strip()
            if not word_text:
                continue
            word_start = max(start, float(word.get("start") if word.get("start") is not None else start))
            word_end = max(
                word_start,
                min(end, float(word.get("end") if word.get("end") is not None else end)),
            )
            words.append(
                {
                    "word": word_text,
                    "start": word_start,
                    "end": word_end,
                    "probability": float(word.get("probability") or 0.0),
                }
            )
        normalized.append(
            {
                "start": start,
                "end": end,
                "text": text,
                "words": words,
                "avg_logprob": float(segment.get("avg_logprob") or 0.0),
                "source_method": "speech",
            }
        )
    return normalized


def process_video(
    *,
    job_id: str,
    video_path: Path,
    mode: int,
    whisper_model: str,
    voice_type: str,
    voice_mode: str,
    speech_rate: float,
    background_music_path: Optional[Path],
    keep_original_audio: bool,
    original_audio_volume: float,
    music_volume: float,
    subtitle_style: Optional[dict[str, Any]],
    subtitle_source: str,
    ocr_sample_fps: float,
    ocr_roi_top: float,
    ocr_roi_bottom: float,
    generate_tiktok_post: bool = True,
    tiktok_max_summary_chars: int = 350,
    tiktok_hashtag_count: int = 6,
    auto_publish_tiktok: bool = False,
    tiktok_privacy_level: str = "SELF_ONLY",
    tiktok_publish_at: str | None = None,
) -> None:
    _job_context.job_id = job_id
    work_dir = WORK_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    subtitle_style = {**DEFAULT_SUB_STYLE, **(subtitle_style or {})}
    if BUNDLED_FONT.is_file():
        subtitle_style["font_name"] = "Noto Sans CJK SC"
    try:
        ensure_job_active(job_id)
        ocr_segments: list[dict[str, Any]] = []
        if subtitle_source in {"auto", "burned"} and not SETTINGS.paddle_ocr_enabled:
            message = "OCR is disabled on this Linux ARM64 runtime; using Whisper speech recognition"
            if subtitle_source == "burned":
                raise RuntimeError(
                    "Burned-subtitle OCR is disabled on this runtime because PaddleOCR crashes the server. "
                    "Choose Whisper or Auto, or set VIDTRANS_ENABLE_PADDLE_OCR=1 only after validating PaddleOCR."
                )
            logger.warning(message)
            update_job(job_id, status="processing", step="transcribing", step_detail=message, progress=0.22)
        elif subtitle_source in {"auto", "burned"}:
            update_job(job_id, status="processing", step="extracting-subtitles", progress=0.12)
            try:
                with _ocr_slots:
                    ensure_job_active(job_id)
                    ocr_segments = extract_burned_subtitle_segments(
                        video_path,
                        work_dir,
                        ffmpeg=FFMPEG,
                        config=OCRConfig(
                            sample_fps=ocr_sample_fps,
                            roi_top=ocr_roi_top,
                            roi_bottom=ocr_roi_bottom,
                        ),
                        progress_callback=make_ocr_progress_callback(job_id),
                    )
            except JobCancelled:
                raise
            except Exception:
                if subtitle_source == "burned":
                    raise
                logger.warning("OCR subtitle extraction failed; using speech transcription", exc_info=True)

        ensure_job_active(job_id)
        update_job(job_id, status="processing", step="transcribing", step_detail=None, progress=0.22)
        with _whisper_slots:
            ensure_job_active(job_id)
            model = get_whisper_model(whisper_model)
            asr_segments = transcribe_chinese_video(model, video_path)
        if ocr_segments:
            segments = annotate_ocr_segments_with_asr(ocr_segments, asr_segments)
        else:
            segments = asr_segments
        if not segments:
            raise RuntimeError(
                "No burned-in Chinese subtitles or audible Chinese speech could be detected. "
                "Check the OCR subtitle region or try a larger Whisper model."
            )

        transcript_path = work_dir / "transcript.json"
        transcript_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")

        update_job(
            job_id,
            step="translating",
            progress=0.4,
            subtitle_source_used="ocr" if ocr_segments else "speech",
            review_cues=sum(1 for segment in segments if segment.get("needs_review")),
        )
        ensure_job_active(job_id)
        translated_segments = translate_segments(segments)
        translation_fallback_cues = sum(
            1 for segment in translated_segments if segment.get("translation_status") == "source_fallback"
        )
        update_job(
            job_id,
            translation_fallback_cues=translation_fallback_cues,
            review_cues=sum(1 for segment in translated_segments if segment.get("needs_review")),
            step_detail=(
                f"Có {translation_fallback_cues} câu cần kiểm tra lại bản dịch"
                if translation_fallback_cues
                else "Đã dịch đầy đủ các câu"
            ),
        )
        video_width, video_height = get_video_dimensions(video_path)
        translated_segments = apply_subtitle_layout(
            translated_segments,
            video_width=video_width,
            video_height=video_height,
            style=subtitle_style,
        )

        srt_path = OUTPUT_DIR / f"{job_id}.srt"
        translation_path = OUTPUT_DIR / f"{job_id}.translation.json"
        ass_path = work_dir / f"{job_id}.ass"
        write_srt(translated_segments, srt_path)
        write_ass(
            translated_segments,
            ass_path,
            subtitle_style,
            video_width=video_width,
            video_height=video_height,
        )

        tiktok_json_path: Path | None = None
        tiktok_text_path: Path | None = None
        tiktok_post = None
        if generate_tiktok_post:
            ensure_job_active(job_id)
            update_job(job_id, step="summarizing", progress=0.47, step_detail=None)
            try:
                tiktok_post = TIKTOK_SUMMARY_PROVIDER.generate(
                    translated_segments,
                    max_summary_chars=tiktok_max_summary_chars,
                    hashtag_count=tiktok_hashtag_count,
                )
                tiktok_json_path, tiktok_text_path = write_tiktok_artifacts(
                    tiktok_post,
                    OUTPUT_DIR,
                    job_id,
                )
            except Exception as exc:
                logger.warning("TikTok summary generation failed for job %s", job_id, exc_info=True)
                update_job(job_id, tiktok_error=str(exc))

        video_duration = get_media_duration(video_path)
        voice_summary: dict[str, int] | None = None
        if mode in (2, 3):
            ensure_job_active(job_id)
            if voice_mode == "auto":
                update_job(job_id, step="routing-voices", progress=0.5)
                try:
                    analysis_audio = extract_voice_analysis_audio(
                        video_path,
                        work_dir / "voice_analysis.wav",
                    )
                    voice_summary = route_segments_by_pitch(
                        translated_segments,
                        analysis_audio,
                        fallback_voice=voice_type,
                    )
                except JobCancelled:
                    raise
                except Exception:
                    logger.warning(
                        "Automatic voice routing failed; using the selected fallback voice",
                        exc_info=True,
                    )
                    voice_summary = route_segments_manually(translated_segments, voice_type)
            else:
                voice_summary = route_segments_manually(translated_segments, voice_type)

        translation_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "source_method": "ocr" if ocr_segments else "speech",
                    "review_cues": sum(1 for segment in translated_segments if segment.get("needs_review")),
                    "voice_routing": {
                        "mode": voice_mode,
                        "fallback_voice": voice_type,
                        "summary": voice_summary,
                    },
                    "segments": translated_segments,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        audio_path = None
        if mode in (2, 3):
            ensure_job_active(job_id)
            update_job(job_id, step="tts", progress=0.55)
            synthesize_tts_segments(
                translated_segments,
                work_dir,
                voice_type,
                speech_rate,
                job_id=job_id,
            )
            ensure_job_active(job_id)
            voice_track = build_voice_track(translated_segments, work_dir, video_duration)

            update_job(job_id, step="mixing-audio", progress=0.75)
            audio_path = mix_audio(
                mode=mode,
                video_path=video_path,
                work_dir=work_dir,
                voice_track=voice_track,
                background_music=background_music_path if mode == 3 else None,
                keep_original_audio=keep_original_audio,
                original_audio_volume=original_audio_volume,
                music_volume=music_volume,
            )

        output_video = OUTPUT_DIR / f"output_{job_id}.mp4"
        ensure_job_active(job_id)
        update_job(job_id, step="rendering", progress=0.9)
        burn_subtitles(video_path, ass_path, output_video, audio_path, translated_segments, subtitle_style, work_dir)
        ensure_job_active(job_id)

        artifact_fields: dict[str, Any] = {
            "output_video": str(output_video.name),
            "subtitle_file": str(srt_path.name),
            "translation_file": str(translation_path.name),
            "transcript_file": str(transcript_path.relative_to(BASE_DIR)),
        }
        if tiktok_json_path and tiktok_text_path:
            artifact_fields.update(
                {
                    "tiktok_json_file": str(tiktok_json_path.name),
                    "tiktok_text_file": str(tiktok_text_path.name),
                }
            )

        if auto_publish_tiktok and tiktok_publish_at and tiktok_post is not None:
            publish_at = datetime.fromisoformat(tiktok_publish_at.replace("Z", "+00:00"))
            if publish_at > datetime.now(timezone.utc):
                update_job(
                    job_id,
                    status="scheduled",
                    step="waiting-tiktok-publish",
                    progress=0.99,
                    step_detail="Video đã sẵn sàng và đang chờ đến lịch đăng TikTok",
                    processing_finished_at=utc_now(),
                    tiktok_publish_at=publish_at.astimezone(timezone.utc).isoformat(),
                    tiktok_publish_status="SCHEDULED",
                    tiktok_publish_title=tiktok_post.title,
                    tiktok_publish_privacy_level=tiktok_privacy_level,
                    job_action="publish_tiktok",
                    publish_request={
                        "video_path": str(output_video),
                        "title": tiktok_post.title,
                        "privacy_level": tiktok_privacy_level,
                    },
                    **artifact_fields,
                )
                return

        tiktok_publish_fields: dict[str, Any] = {}
        if auto_publish_tiktok:
            update_job(
                job_id,
                step="publishing-tiktok",
                progress=0.97,
                step_detail="Đang tải video đã dựng lên TikTok",
                tiktok_publish_status="UPLOADING",
            )
            try:
                if tiktok_post is None:
                    raise TikTokPublisherError(
                        "Không có tiêu đề đã tạo nên chưa thể tự động đăng TikTok"
                    )
                publish_result = TIKTOK_PUBLISHER.publish(
                    output_video,
                    tiktok_post.title,
                    privacy_level=tiktok_privacy_level,
                )
                tiktok_publish_fields = {
                    "tiktok_publish_id": publish_result["publish_id"],
                    "tiktok_publish_title": publish_result["title"],
                    "tiktok_publish_privacy_level": publish_result["privacy_level"],
                    "tiktok_publish_status": publish_result["status"],
                    "tiktok_publish_detail": publish_result.get("status_payload"),
                }
            except Exception as exc:
                logger.warning("TikTok auto publish failed for job %s", job_id, exc_info=True)
                tiktok_publish_fields = {
                    "tiktok_publish_status": "FAILED",
                    "tiktok_publish_error": str(exc),
                }

        completion_fields: dict[str, Any] = {
            "status": "completed",
            "step": "completed",
            "progress": 1.0,
            "finished_at": utc_now(),
            "job_action": None,
            "publish_request": None,
            **artifact_fields,
            **tiktok_publish_fields,
        }
        update_job(job_id, **completion_fields)
    except JobCancelled:
        logger.info("Video processing cancelled for job %s", job_id)
        current = JOB_SERVICE.get(job_id) or {}
        update_job(
            job_id,
            status="cancelled",
            step="cancelled",
            progress=float(current.get("progress", 0.0)),
            step_detail="Đã hủy theo yêu cầu",
            finished_at=utc_now(),
        )
    except Exception as exc:
        logger.exception("Video processing failed for job %s", job_id)
        update_job(
            job_id,
            status="failed",
            step="failed",
            error=public_error_message(exc),
            finished_at=utc_now(),
        )
    finally:
        if hasattr(_job_context, "job_id"):
            del _job_context.job_id


def publish_scheduled_tiktok(job_id: str, publish_request: dict[str, Any]) -> None:
    """Publish an already-rendered video claimed from the durable schedule."""

    _job_context.job_id = job_id
    try:
        ensure_job_active(job_id)
        video_path = Path(str(publish_request["video_path"]))
        title = str(publish_request["title"])
        privacy_level = str(publish_request.get("privacy_level") or "SELF_ONLY")
        update_job(
            job_id,
            status="processing",
            step="publishing-tiktok",
            progress=0.99,
            step_detail="Đã đến lịch, đang tải video lên TikTok",
            tiktok_publish_status="UPLOADING",
            tiktok_publish_error=None,
        )
        publish_result = TIKTOK_PUBLISHER.publish(
            video_path,
            title,
            privacy_level=privacy_level,
        )
        update_job(
            job_id,
            status="completed",
            step="completed",
            progress=1.0,
            step_detail="Đã gửi video lên TikTok theo lịch",
            finished_at=utc_now(),
            job_action=None,
            publish_request=None,
            tiktok_publish_id=publish_result["publish_id"],
            tiktok_publish_title=publish_result["title"],
            tiktok_publish_privacy_level=publish_result["privacy_level"],
            tiktok_publish_status=publish_result["status"],
            tiktok_publish_detail=publish_result.get("status_payload"),
        )
    except JobCancelled:
        current = JOB_SERVICE.get(job_id) or {}
        update_job(
            job_id,
            status="cancelled",
            step="cancelled",
            progress=float(current.get("progress", 0.99)),
            step_detail="Đã hủy lịch đăng TikTok",
            finished_at=utc_now(),
            job_action=None,
            publish_request=None,
        )
    except Exception as exc:
        logger.warning("Scheduled TikTok publish failed for job %s", job_id, exc_info=True)
        update_job(
            job_id,
            status="completed",
            step="completed",
            progress=1.0,
            step_detail="Video đã xử lý xong nhưng đăng TikTok theo lịch thất bại",
            finished_at=utc_now(),
            job_action=None,
            publish_request=None,
            tiktok_publish_status="FAILED",
            tiktok_publish_error=str(exc),
        )
    finally:
        if hasattr(_job_context, "job_id"):
            del _job_context.job_id


def prepare_job_source_video(job_id: str, resume_request: dict[str, Any]) -> Path:
    """Return a local source path, downloading a persisted social URL when needed."""

    video_path = Path(str(resume_request["video_path"]))
    source_url_value = resume_request.get("source_url")
    if video_path.is_file():
        try:
            validate_video_file(video_path, ffprobe=FFPROBE)
        except InvalidVideoError:
            # A failed/aborted social download can leave a zero-byte placeholder.
            # Remove it and let the persisted URL download again on this run.
            if not source_url_value:
                raise
            video_path.unlink(missing_ok=True)
        else:
            update_job(
                job_id,
                status="processing",
                step="source-ready",
                step_detail=(
                    "Video nguồn đã tải xong, chuẩn bị nhận diện"
                    if source_url_value
                    else "Video upload đã sẵn sàng, chuẩn bị nhận diện"
                ),
                progress=0.1,
            )
            return video_path

    if not source_url_value:
        raise FileNotFoundError("The uploaded source video is no longer available for restart recovery")
    source_url = str(source_url_value)
    ensure_job_active(job_id)
    update_job(
        job_id,
        status="processing",
        step="downloading-source",
        step_detail=f"Đang kết nối {social_platform(source_url)}",
        progress=0.01,
    )
    last_percent = -1

    def report_download_progress(downloaded: int, total: int | None) -> None:
        nonlocal last_percent
        ensure_job_active(job_id)
        if total and total > 0:
            percent = min(100, int(downloaded * 100 / total))
            if percent == last_percent:
                return
            last_percent = percent
            progress = 0.01 + (0.08 * percent / 100)
            detail = f"Đã tải {percent}% từ {social_platform(source_url)}"
        else:
            megabytes = downloaded / (1024 * 1024)
            bucket = int(megabytes)
            if bucket == last_percent:
                return
            last_percent = bucket
            progress = 0.03
            detail = f"Đã tải {megabytes:.1f} MB từ {social_platform(source_url)}"
        update_job(job_id, progress=progress, step_detail=detail)

    result = SOCIAL_VIDEO_DOWNLOADER.download(
        source_url,
        UPLOAD_DIR / job_id,
        progress_callback=report_download_progress,
        cancel_requested=lambda: JOB_SERVICE.is_cancel_requested(job_id),
        cookie_file=(
            Path(str(resume_request["source_cookie_path"]))
            if resume_request.get("source_cookie_path")
            else None
        ),
    )
    try:
        validate_video_file(result.path, ffprobe=FFPROBE)
    except InvalidVideoError:
        result.path.unlink(missing_ok=True)
        raise
    stored_request = {**resume_request, "video_path": str(result.path), "source_url": result.source_url}
    source_cookie_value = stored_request.pop("source_cookie_path", None)
    if source_cookie_value:
        source_cookie_path = Path(str(source_cookie_value)).resolve()
        if source_cookie_path.parent == UPLOAD_DIR.resolve():
            source_cookie_path.unlink(missing_ok=True)
    update_job(
        job_id,
        filename=result.display_filename,
        source_url=result.source_url,
        source_platform=result.platform,
        source_video_id=result.video_id,
        source_duration=result.duration,
        resume_request=stored_request,
        step="source-ready",
        progress=0.1,
        step_detail="Video nguồn đã tải xong, chuẩn bị nhận diện",
    )
    resume_request.clear()
    resume_request.update(stored_request)
    return result.path


def resume_process_video(job_id: str, resume_request: dict[str, Any]) -> None:
    """Run a persisted job request after the API process has restarted."""
    try:
        job_action = str(resume_request.pop("_job_action", "process_video"))
        if job_action == "publish_tiktok":
            publish_scheduled_tiktok(job_id, resume_request)
            return
        if job_action != "process_video":
            raise ValueError("Stored background action is invalid")
        video_path = prepare_job_source_video(job_id, resume_request)
        background_music_value = resume_request.get("background_music_path")
        background_music_path = Path(str(background_music_value)) if background_music_value else None
        if int(resume_request["mode"]) == 3 and (
            background_music_path is None or not background_music_path.is_file()
        ):
            raise FileNotFoundError("The uploaded background music is no longer available for restart recovery")

        subtitle_style = resume_request.get("subtitle_style")
        if subtitle_style is not None and not isinstance(subtitle_style, dict):
            raise ValueError("Stored subtitle style is invalid")
        process_video(
            job_id=job_id,
            video_path=video_path,
            mode=int(resume_request["mode"]),
            whisper_model=str(resume_request["whisper_model"]),
            voice_type=str(resume_request["voice_type"]),
            voice_mode=str(resume_request["voice_mode"]),
            speech_rate=float(resume_request["speech_rate"]),
            background_music_path=background_music_path,
            keep_original_audio=bool(resume_request["keep_original_audio"]),
            original_audio_volume=float(resume_request["original_audio_volume"]),
            music_volume=float(resume_request["music_volume"]),
            subtitle_style=subtitle_style,
            subtitle_source=str(resume_request["subtitle_source"]),
            ocr_sample_fps=float(resume_request["ocr_sample_fps"]),
            ocr_roi_top=float(resume_request["ocr_roi_top"]),
            ocr_roi_bottom=float(resume_request["ocr_roi_bottom"]),
            generate_tiktok_post=bool(resume_request.get("generate_tiktok_post", True)),
            tiktok_max_summary_chars=int(resume_request.get("tiktok_max_summary_chars", 350)),
            tiktok_hashtag_count=int(resume_request.get("tiktok_hashtag_count", 6)),
            auto_publish_tiktok=bool(resume_request.get("auto_publish_tiktok", False)),
            tiktok_privacy_level=str(resume_request.get("tiktok_privacy_level", "SELF_ONLY")),
            tiktok_publish_at=(
                str(resume_request["tiktok_publish_at"])
                if resume_request.get("tiktok_publish_at")
                else None
            ),
        )
    except (JobCancelled, SocialVideoDownloadCancelled) as exc:
        logger.info("Source download cancelled for job %s", job_id)
        current = JOB_SERVICE.get(job_id) or {}
        update_job(
            job_id,
            status="cancelled",
            step="cancelled",
            progress=float(current.get("progress", 0.0)),
            step_detail=str(exc),
            finished_at=utc_now(),
        )
    except (KeyError, TypeError, ValueError, FileNotFoundError, SocialVideoDownloadError) as exc:
        logger.warning("Unable to resume job %s: %s", job_id, exc)
        update_job(
            job_id,
            status="failed",
            step="failed",
            error=public_error_message(exc),
            finished_at=utc_now(),
        )


@app.on_event("startup")
async def start_job_scheduler() -> None:
    if os.environ.get("VIDTRANS_RECOVER_INTERRUPTED_JOBS", "1").lower() in {"1", "true", "yes"}:
        requeued = JOB_SERVICE.requeue_interrupted()
        if requeued:
            logger.info("Requeued %d interrupted jobs after server restart", len(requeued))
    scheduler = JobScheduler(
        JOB_SERVICE,
        resume_process_video,
        concurrency=SETTINGS.worker_concurrency,
    )
    app.state.job_scheduler = scheduler
    await scheduler.start()


@app.on_event("shutdown")
async def stop_job_scheduler() -> None:
    scheduler = getattr(app.state, "job_scheduler", None)
    if scheduler is not None:
        await scheduler.stop()
    await asyncio.to_thread(DOUYIN_QR_AUTH.close)


def notify_scheduler() -> None:
    scheduler = getattr(app.state, "job_scheduler", None)
    if scheduler is not None:
        scheduler.notify()


def validate_tiktok_auto_publish(request: ProcessingRequest) -> None:
    if not request.tiktok.auto_publish:
        return
    if not TIKTOK_PUBLISHER.connection_status()["connected"]:
        raise HTTPException(status_code=400, detail="Hãy kết nối tài khoản TikTok trước khi bật tự động đăng")
    try:
        creator = TIKTOK_PUBLISHER.creator_info()
    except TikTokPublisherError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    privacy_options = creator.get("privacy_level_options") or []
    if request.tiktok.privacy_level not in privacy_options:
        raise HTTPException(
            status_code=400,
            detail="Tài khoản TikTok không hỗ trợ mức quyền riêng tư đã chọn",
        )


@app.get("/api/v1/auth/status")
def get_auth_status(request: Request) -> JSONResponse:
    payload = AUTH_MANAGER.status()
    payload["authenticated"] = False
    payload["username"] = None
    if AUTH_MANAGER.config.configured:
        token, _, malformed_header = request_access_token(request)
        if token and not malformed_header:
            try:
                claims = AUTH_MANAGER.verify_token(token)
                payload["authenticated"] = True
                payload["username"] = claims["sub"]
            except (AuthConfigurationError, InvalidTokenError):
                pass
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.post("/api/v1/auth/token")
def create_auth_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> JSONResponse:
    client_id = auth_client_id(request)
    try:
        authenticated = AUTH_MANAGER.authenticate(form_data.username, form_data.password, client_id)
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LoginRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "300"}) from exc
    if not authenticated:
        raise HTTPException(
            status_code=401,
            detail="Tên đăng nhập hoặc mật khẩu không đúng",
            headers={"WWW-Authenticate": 'Bearer scope="admin"'},
        )
    token, expires_in = AUTH_MANAGER.issue_token(form_data.username)
    response = JSONResponse(
        {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": expires_in,
            "scope": "admin",
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
    response.set_cookie(
        key=AUTH_MANAGER.config.cookie_name,
        value=token,
        max_age=expires_in,
        httponly=True,
        secure=AUTH_MANAGER.config.cookie_secure,
        samesite="strict",
        path="/",
    )
    return response


@app.delete("/api/v1/auth/session")
def delete_auth_session() -> JSONResponse:
    response = JSONResponse(
        {"authenticated": False, "message": "Đã đăng xuất VidTrans"},
        headers={"Cache-Control": "no-store"},
    )
    response.delete_cookie(
        key=AUTH_MANAGER.config.cookie_name,
        path="/",
        secure=AUTH_MANAGER.config.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return response


@app.get("/api/v1/douyin-auth")
def get_douyin_auth_status() -> JSONResponse:
    return JSONResponse(DOUYIN_QR_AUTH.auth_status())


@app.post("/api/v1/douyin-auth/qr")
def start_douyin_qr_login() -> JSONResponse:
    return JSONResponse(DOUYIN_QR_AUTH.start(), status_code=201)


@app.get("/api/v1/douyin-auth/qr/{session_id}")
def get_douyin_qr_login(session_id: str) -> JSONResponse:
    try:
        return JSONResponse(DOUYIN_QR_AUTH.snapshot(session_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Phiên đăng nhập QR không còn tồn tại") from exc


@app.post("/api/v1/douyin-auth/qr/{session_id}/phone")
def submit_douyin_phone(
    session_id: str,
    country_code: str = Form("+86"),
    phone: str = Form(...),
) -> JSONResponse:
    try:
        return JSONResponse(DOUYIN_QR_AUTH.submit_phone(session_id, country_code, phone))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Phiên đăng nhập Douyin không còn tồn tại") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/douyin-auth/qr/{session_id}/otp")
def submit_douyin_otp(
    session_id: str,
    otp: str = Form(...),
) -> JSONResponse:
    try:
        return JSONResponse(DOUYIN_QR_AUTH.submit_otp(session_id, otp))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Phiên đăng nhập Douyin không còn tồn tại") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/douyin-auth/qr/{session_id}/image")
def get_douyin_qr_image(session_id: str) -> FileResponse:
    try:
        image_path = DOUYIN_QR_AUTH.image_path(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Ảnh QR chưa sẵn sàng") from exc
    return FileResponse(
        image_path,
        media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.delete("/api/v1/douyin-auth/qr/{session_id}")
def cancel_douyin_qr_login(session_id: str) -> JSONResponse:
    DOUYIN_QR_AUTH.cancel(session_id)
    return JSONResponse({"session_id": session_id, "cancelled": True})


@app.delete("/api/v1/douyin-auth")
def logout_douyin() -> JSONResponse:
    DOUYIN_QR_AUTH.logout()
    return JSONResponse({"authenticated": False, "message": "Đã xóa phiên đăng nhập Douyin"})


@app.get("/api/v1/tiktok-auth")
def get_tiktok_auth_status() -> JSONResponse:
    return JSONResponse(TIKTOK_PUBLISHER.connection_status())


@app.get("/api/v1/tiktok-auth/connect")
def connect_tiktok() -> JSONResponse:
    try:
        return JSONResponse({"authorization_url": TIKTOK_PUBLISHER.authorization_url()})
    except TikTokPublisherError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/tiktok-auth/callback")
def tiktok_auth_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
) -> RedirectResponse:
    try:
        if error:
            raise TikTokPublisherError(error_description or error)
        TIKTOK_PUBLISHER.exchange_code(code, state)
        return RedirectResponse(url="/?tiktok=connected#create", status_code=303)
    except TikTokPublisherError as exc:
        message = quote_plus(str(exc))
        return RedirectResponse(url=f"/?tiktok_error={message}#create", status_code=303)


@app.delete("/api/v1/tiktok-auth")
def disconnect_tiktok() -> JSONResponse:
    TIKTOK_PUBLISHER.disconnect()
    return JSONResponse(TIKTOK_PUBLISHER.connection_status())


@app.post("/process-video")
@app.post("/convert")
async def process_video_endpoint(
    file: UploadFile = File(...),
    mode: int = Form(...),
    voice_type: str = Form("female"),
    voice_mode: str = Form("auto"),
    speech_rate: float = Form(1.0),
    background_music: UploadFile | None = File(default=None),
    whisper_model: str = Form("base"),
    keep_original_audio: bool = Form(True),
    original_audio_volume: float = Form(0.18),
    music_volume: float = Form(0.28),
    subtitle_style: str | None = Form(default=None),
    subtitle_source: str = Form("speech"),
    ocr_sample_fps: float = Form(5.0),
    ocr_roi_top: float = Form(0.68),
    ocr_roi_bottom: float = Form(0.96),
    generate_tiktok_post: bool = Form(True),
    tiktok_max_summary_chars: int = Form(350),
    tiktok_hashtag_count: int = Form(6),
    auto_publish_tiktok: bool = Form(False),
    tiktok_privacy_level: str = Form("SELF_ONLY"),
    tiktok_publish_at: str | None = Form(default=None),
) -> JSONResponse:
    video_ext = Path(file.filename or "video.mp4").suffix.lower() or ".mp4"
    if video_ext not in SUPPORTED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Định dạng video không được hỗ trợ: {video_ext}")
    try:
        request = ProcessingRequest.from_form(
            mode=mode,
            subtitle_source=subtitle_source,
            ocr_sample_fps=ocr_sample_fps,
            ocr_roi_top=ocr_roi_top,
            ocr_roi_bottom=ocr_roi_bottom,
            voice_mode=voice_mode,
            voice_type=voice_type,
            generate_tiktok_post=generate_tiktok_post,
            tiktok_max_summary_chars=tiktok_max_summary_chars,
            tiktok_hashtag_count=tiktok_hashtag_count,
            auto_publish_tiktok=auto_publish_tiktok,
            tiktok_privacy_level=tiktok_privacy_level,
            tiktok_publish_at=tiktok_publish_at,
        )
        OCRConfig(
            sample_fps=request.ocr.sample_fps,
            roi_top=request.ocr.roi_top,
            roi_bottom=request.ocr.roi_bottom,
        ).validate()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request.mode is ProcessingMode.DUBBED_WITH_MUSIC and background_music is None:
        raise HTTPException(status_code=400, detail="background_music is required for mode 3")

    job_id = uuid.uuid4().hex[:8]
    validate_tiktok_auto_publish(request)
    video_path = UPLOAD_DIR / f"{job_id}{video_ext}"
    with video_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    try:
        validate_video_file(video_path, ffprobe=FFPROBE)
    except InvalidVideoError as exc:
        video_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    background_music_path = None
    if background_music is not None:
        music_ext = Path(background_music.filename or "music.mp3").suffix or ".mp3"
        background_music_path = UPLOAD_DIR / f"{job_id}_bgm{music_ext}"
        with background_music_path.open("wb") as buffer:
            shutil.copyfileobj(background_music.file, buffer)

    style_payload = None
    if subtitle_style:
        try:
            style_payload = json.loads(subtitle_style)
            if not isinstance(style_payload, dict):
                raise ValueError("subtitle_style must be a JSON object")
            SubtitleLayoutOptions.from_style(style_payload)
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    resume_request = {
        "video_path": str(video_path),
        "mode": int(request.mode),
        "whisper_model": whisper_model,
        "voice_type": request.fallback_voice.value,
        "voice_mode": request.voice_mode.value,
        "speech_rate": speech_rate,
        "background_music_path": str(background_music_path) if background_music_path else None,
        "keep_original_audio": keep_original_audio,
        "original_audio_volume": original_audio_volume,
        "music_volume": music_volume,
        "subtitle_style": style_payload,
        "subtitle_source": request.subtitle_source.value,
        "ocr_sample_fps": request.ocr.sample_fps,
        "ocr_roi_top": request.ocr.roi_top,
        "ocr_roi_bottom": request.ocr.roi_bottom,
        "generate_tiktok_post": request.tiktok.enabled,
        "tiktok_max_summary_chars": request.tiktok.max_summary_chars,
        "tiktok_hashtag_count": request.tiktok.hashtag_count,
        "auto_publish_tiktok": request.tiktok.auto_publish,
        "tiktok_privacy_level": request.tiktok.privacy_level,
        "tiktok_publish_at": request.tiktok.publish_at,
    }

    JOB_SERVICE.create(job_id, {
        "status": "queued",
        "step": "queued",
        "progress": 0.0,
        "mode": int(request.mode),
        "filename": file.filename,
        "subtitle_source": request.subtitle_source.value,
        "voice_routing": {
            "mode": request.voice_mode.value,
            "fallback_voice": request.fallback_voice.value,
        },
        "tiktok": {
            "enabled": request.tiktok.enabled,
            "max_summary_chars": request.tiktok.max_summary_chars,
            "hashtag_count": request.tiktok.hashtag_count,
            "auto_publish": request.tiktok.auto_publish,
            "privacy_level": request.tiktok.privacy_level,
            "publish_at": request.tiktok.publish_at,
        },
        "resume_request": resume_request,
        "ocr_config": {
            "sample_fps": request.ocr.sample_fps,
            "roi_top": request.ocr.roi_top,
            "roi_bottom": request.ocr.roi_bottom,
        },
    })
    notify_scheduler()

    return JSONResponse(
        {
            "job_id": job_id,
            "processing_status": "queued",
            "status_url": f"/status/{job_id}",
            "video_url": None,
            "subtitle_url": None,
        }
    )


@app.post("/api/v1/batches")
async def create_batch_endpoint(
    files: list[UploadFile] | None = File(default=None),
    source_links: str = Form(""),
    source_cookies: UploadFile | None = File(default=None),
    batch_name: str = Form("Batch mới"),
    mode: int = Form(1),
    voice_type: str = Form("female"),
    voice_mode: str = Form("auto"),
    speech_rate: float = Form(1.0),
    background_music: UploadFile | None = File(default=None),
    whisper_model: str = Form("base"),
    keep_original_audio: bool = Form(True),
    original_audio_volume: float = Form(0.18),
    music_volume: float = Form(0.28),
    subtitle_style: str | None = Form(default=None),
    subtitle_source: str = Form("speech"),
    ocr_sample_fps: float = Form(5.0),
    ocr_roi_top: float = Form(0.68),
    ocr_roi_bottom: float = Form(0.96),
    generate_tiktok_post: bool = Form(True),
    tiktok_max_summary_chars: int = Form(350),
    tiktok_hashtag_count: int = Form(6),
    auto_publish_tiktok: bool = Form(False),
    tiktok_privacy_level: str = Form("SELF_ONLY"),
    tiktok_publish_at: str | None = Form(default=None),
) -> JSONResponse:
    # Browsers serialize an untouched <input type="file"> as an empty UploadFile.
    # Ignore only that unnamed placeholder; named zero-byte uploads are still
    # validated below and reported to the user as invalid videos.
    uploads = [upload for upload in (files or []) if (upload.filename or "").strip()]
    try:
        source_urls = extract_social_video_urls(source_links, limit=50)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if source_links.strip() and not source_urls:
        raise HTTPException(status_code=400, detail="Không tìm thấy link TikTok hoặc Douyin hợp lệ")
    cookie_payload: bytes | None = None
    if source_cookies is not None and source_cookies.filename:
        if not source_urls:
            raise HTTPException(status_code=400, detail="Chỉ dùng cookies.txt khi batch có link TikTok/Douyin")
        cookie_payload = await source_cookies.read(512 * 1024 + 1)
        if len(cookie_payload) > 512 * 1024:
            raise HTTPException(status_code=400, detail="File cookies.txt không được vượt quá 512 KB")
        if not cookie_payload or b"\x00" in cookie_payload:
            raise HTTPException(status_code=400, detail="File cookies.txt không hợp lệ")
    elif source_urls and DOUYIN_QR_AUTH.cookie_path.is_file():
        cookie_payload = DOUYIN_QR_AUTH.cookie_path.read_bytes()
    total_sources = len(uploads) + len(source_urls)
    if total_sources < 1 or total_sources > 50:
        raise HTTPException(
            status_code=400,
            detail="Mỗi batch phải có tổng cộng từ 1 đến 50 file hoặc link video",
        )
    invalid_extensions = sorted(
        {
            Path(upload.filename or "video.mp4").suffix.lower()
            for upload in uploads
            if (Path(upload.filename or "video.mp4").suffix.lower() or ".mp4")
            not in SUPPORTED_VIDEO_EXTENSIONS
        }
    )
    if invalid_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Định dạng video không được hỗ trợ: {', '.join(invalid_extensions)}",
        )
    try:
        request = ProcessingRequest.from_form(
            mode=mode,
            subtitle_source=subtitle_source,
            ocr_sample_fps=ocr_sample_fps,
            ocr_roi_top=ocr_roi_top,
            ocr_roi_bottom=ocr_roi_bottom,
            voice_mode=voice_mode,
            voice_type=voice_type,
            generate_tiktok_post=generate_tiktok_post,
            tiktok_max_summary_chars=tiktok_max_summary_chars,
            tiktok_hashtag_count=tiktok_hashtag_count,
            auto_publish_tiktok=auto_publish_tiktok,
            tiktok_privacy_level=tiktok_privacy_level,
            tiktok_publish_at=tiktok_publish_at,
        )
        OCRConfig(
            sample_fps=request.ocr.sample_fps,
            roi_top=request.ocr.roi_top,
            roi_bottom=request.ocr.roi_bottom,
        ).validate()
        style_payload = json.loads(subtitle_style) if subtitle_style else None
        if style_payload is not None and not isinstance(style_payload, dict):
            raise ValueError("subtitle_style must be a JSON object")
        SubtitleLayoutOptions.from_style(style_payload or {})
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request.mode is ProcessingMode.DUBBED_WITH_MUSIC and background_music is None:
        raise HTTPException(status_code=400, detail="background_music is required for mode 3")
    validate_tiktok_auto_publish(request)

    batch_id = f"batch_{uuid.uuid4().hex[:10]}"
    normalized_name = " ".join(batch_name.split())[:120] or f"Batch {batch_id[-6:]}"
    background_music_path: Path | None = None
    if background_music is not None:
        music_ext = Path(background_music.filename or "music.mp3").suffix or ".mp3"
        background_music_path = UPLOAD_DIR / f"{batch_id}_bgm{music_ext}"
        with background_music_path.open("wb") as buffer:
            shutil.copyfileobj(background_music.file, buffer)

    batch_config = {
        "mode": int(request.mode),
        "subtitle_source": request.subtitle_source.value,
        "voice_mode": request.voice_mode.value,
        "voice_type": request.fallback_voice.value,
        "speech_rate": speech_rate,
        "whisper_model": whisper_model,
        "keep_original_audio": keep_original_audio,
        "original_audio_volume": original_audio_volume,
        "music_volume": music_volume,
        "subtitle_style": style_payload,
        "ocr_sample_fps": request.ocr.sample_fps,
        "ocr_roi_top": request.ocr.roi_top,
        "ocr_roi_bottom": request.ocr.roi_bottom,
        "generate_tiktok_post": request.tiktok.enabled,
        "tiktok_max_summary_chars": request.tiktok.max_summary_chars,
        "tiktok_hashtag_count": request.tiktok.hashtag_count,
        "auto_publish_tiktok": request.tiktok.auto_publish,
        "tiktok_privacy_level": request.tiktok.privacy_level,
        "tiktok_publish_at": request.tiktok.publish_at,
        "background_music_path": str(background_music_path) if background_music_path else None,
    }
    prepared_uploads: list[tuple[str, UploadFile, Path]] = []
    try:
        for upload in uploads:
            upload_job_id = uuid.uuid4().hex[:8]
            video_ext = Path(upload.filename or "video.mp4").suffix.lower() or ".mp4"
            upload_video_path = UPLOAD_DIR / f"{upload_job_id}{video_ext}"
            with upload_video_path.open("wb") as buffer:
                shutil.copyfileobj(upload.file, buffer)
            validate_video_file(upload_video_path, ffprobe=FFPROBE)
            prepared_uploads.append((upload_job_id, upload, upload_video_path))
    except InvalidVideoError as exc:
        for _, _, prepared_path in prepared_uploads:
            prepared_path.unlink(missing_ok=True)
        # The failing file is not appended to prepared_uploads yet.
        if "upload_video_path" in locals():
            upload_video_path.unlink(missing_ok=True)
        if background_music_path is not None:
            background_music_path.unlink(missing_ok=True)
        invalid_name = upload.filename or "video"
        raise HTTPException(status_code=400, detail=f"{invalid_name}: {exc}") from exc

    JOB_SERVICE.create_batch(batch_id, name=normalized_name, config=batch_config)

    created_jobs: list[dict[str, Any]] = []
    for job_id, upload, video_path in prepared_uploads:
        resume_request = {**batch_config, "video_path": str(video_path)}
        JOB_SERVICE.create(
            job_id,
            {
                "status": "queued",
                "step": "queued",
                "step_detail": "Video đã tải lên máy chủ, đang chờ xử lý",
                "progress": 0.0,
                "mode": int(request.mode),
                "filename": upload.filename,
                "subtitle_source": request.subtitle_source.value,
                "voice_routing": {
                    "mode": request.voice_mode.value,
                    "fallback_voice": request.fallback_voice.value,
                },
                "tiktok": {
                    "enabled": request.tiktok.enabled,
                    "max_summary_chars": request.tiktok.max_summary_chars,
                    "hashtag_count": request.tiktok.hashtag_count,
                    "auto_publish": request.tiktok.auto_publish,
                    "privacy_level": request.tiktok.privacy_level,
                    "publish_at": request.tiktok.publish_at,
                },
                "resume_request": resume_request,
                "ocr_config": {
                    "sample_fps": request.ocr.sample_fps,
                    "roi_top": request.ocr.roi_top,
                    "roi_bottom": request.ocr.roi_bottom,
                },
            },
        )
        JOB_SERVICE.attach_to_batch(job_id, batch_id)
        created_jobs.append({"job_id": job_id, "filename": upload.filename, "status": "queued"})

    for source_url in source_urls:
        job_id = uuid.uuid4().hex[:8]
        platform = social_platform(source_url)
        initial_filename = f"Video {platform} · {job_id}"
        source_cookie_path: Path | None = None
        if cookie_payload is not None:
            source_cookie_path = UPLOAD_DIR / f"{job_id}.cookies.txt"
            with source_cookie_path.open("wb") as cookie_buffer:
                cookie_buffer.write(cookie_payload)
            try:
                source_cookie_path.chmod(0o600)
            except OSError:
                logger.warning("Unable to restrict cookie file permissions for job %s", job_id)
        resume_request = {
            **batch_config,
            "video_path": str(UPLOAD_DIR / f"{job_id}.source"),
            "source_url": source_url,
            "source_cookie_path": str(source_cookie_path) if source_cookie_path else None,
        }
        JOB_SERVICE.create(
            job_id,
            {
                "status": "queued",
                "step": "queued",
                "step_detail": f"Đang chờ tải video từ {platform}",
                "progress": 0.0,
                "mode": int(request.mode),
                "filename": initial_filename,
                "source_url": source_url,
                "source_platform": platform,
                "subtitle_source": request.subtitle_source.value,
                "voice_routing": {
                    "mode": request.voice_mode.value,
                    "fallback_voice": request.fallback_voice.value,
                },
                "tiktok": {
                    "enabled": request.tiktok.enabled,
                    "max_summary_chars": request.tiktok.max_summary_chars,
                    "hashtag_count": request.tiktok.hashtag_count,
                    "auto_publish": request.tiktok.auto_publish,
                    "privacy_level": request.tiktok.privacy_level,
                    "publish_at": request.tiktok.publish_at,
                },
                "resume_request": resume_request,
                "ocr_config": {
                    "sample_fps": request.ocr.sample_fps,
                    "roi_top": request.ocr.roi_top,
                    "roi_bottom": request.ocr.roi_bottom,
                },
            },
        )
        JOB_SERVICE.attach_to_batch(job_id, batch_id)
        created_jobs.append(
            {
                "job_id": job_id,
                "filename": initial_filename,
                "source_url": source_url,
                "source_platform": platform,
                "status": "queued",
            }
        )

    notify_scheduler()
    return JSONResponse(
        {
            "batch_id": batch_id,
            "name": normalized_name,
            "total_jobs": len(created_jobs),
            "jobs": created_jobs,
        },
        status_code=201,
    )


def job_response(job_id: str, job: dict[str, Any], queue_positions: dict[str, int] | None = None) -> dict[str, Any]:
    response = {
        "job_id": job_id,
        **{key: value for key, value in job.items() if key != "resume_request"},
    }
    if response.get("error"):
        response["error"] = public_error_message(str(response["error"]))
    if queue_positions and job_id in queue_positions:
        response["queue_position"] = queue_positions[job_id]
    if job.get("output_video"):
        response["video_url"] = f"/download/{job['output_video']}"
    if job.get("subtitle_file"):
        response["subtitle_url"] = f"/download/{job['subtitle_file']}"
    if job.get("translation_file"):
        response["translation_url"] = f"/download/{job['translation_file']}"
    if job.get("tiktok_json_file"):
        response["tiktok_json_url"] = f"/download/{job['tiktok_json_file']}"
    if job.get("tiktok_text_file"):
        response["tiktok_text_url"] = f"/download/{job['tiktok_text_file']}"
    if any(job.get(key) for key in ("output_video", "subtitle_file", "translation_file")):
        response["download_all_url"] = f"/api/v1/jobs/{job_id}/download-all"
    return response


@app.get("/api/v1/jobs")
def list_jobs_endpoint(
    status: str | None = Query(default=None),
    batch_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    jobs, total = JOB_SERVICE.list_jobs(
        status=status,
        batch_id=batch_id,
        search=search,
        limit=limit,
        offset=offset,
    )
    positions = JOB_SERVICE.queue_positions()
    items = [
        job_response(str(job["job_id"]), job, positions)
        for job in jobs
    ]
    return JSONResponse({"items": items, "total": total, "limit": limit, "offset": offset})


@app.get("/api/v1/jobs/{job_id}")
def get_job_endpoint(job_id: str) -> JSONResponse:
    job = JOB_SERVICE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(job_response(job_id, job, JOB_SERVICE.queue_positions()))


@app.post("/api/v1/jobs/{job_id}/refresh-tiktok-status")
def refresh_tiktok_publish_status(job_id: str) -> JSONResponse:
    job = JOB_SERVICE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    publish_id = str(job.get("tiktok_publish_id") or "")
    if not publish_id:
        raise HTTPException(status_code=409, detail="Job chưa gửi video lên TikTok")
    try:
        detail = TIKTOK_PUBLISHER.fetch_status(publish_id)
    except TikTokPublisherError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    update_job(
        job_id,
        tiktok_publish_status=detail.get("status") or job.get("tiktok_publish_status") or "SUBMITTED",
        tiktok_publish_detail=detail,
    )
    refreshed = JOB_SERVICE.get(job_id) or job
    return JSONResponse(job_response(job_id, refreshed, JOB_SERVICE.queue_positions()))


@app.get("/api/v1/jobs/{job_id}/download-all")
def download_all_job_artifacts(job_id: str) -> FileResponse:
    job = JOB_SERVICE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    artifact_keys = (
        "output_video",
        "subtitle_file",
        "translation_file",
        "tiktok_json_file",
        "tiktok_text_file",
    )
    artifacts: list[Path] = []
    for key in artifact_keys:
        filename = job.get(key)
        if not filename:
            continue
        target = (OUTPUT_DIR / str(filename)).resolve()
        if target.parent == OUTPUT_DIR.resolve() and target.is_file():
            artifacts.append(target)
    if not artifacts:
        raise HTTPException(status_code=404, detail="job chưa có artifact để tải")
    archive_path = OUTPUT_DIR / f"{job_id}.artifacts.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for artifact in artifacts:
            archive.write(artifact, arcname=artifact.name)
    return FileResponse(archive_path, filename=archive_path.name)


@app.get("/api/v1/batches")
def list_batches_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    batches, total = JOB_SERVICE.list_batches(limit=limit, offset=offset)
    for batch in batches:
        if isinstance(batch.get("config"), dict):
            batch["config"].pop("background_music_path", None)
    return JSONResponse({"items": batches, "total": total, "limit": limit, "offset": offset})


@app.get("/api/v1/batches/{batch_id}")
def get_batch_endpoint(batch_id: str) -> JSONResponse:
    batch = JOB_SERVICE.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    if isinstance(batch.get("config"), dict):
        batch["config"].pop("background_music_path", None)
    jobs, total = JOB_SERVICE.list_jobs(batch_id=batch_id, limit=200)
    positions = JOB_SERVICE.queue_positions()
    batch["jobs"] = [job_response(str(job["job_id"]), job, positions) for job in jobs]
    batch["total_jobs"] = total
    statuses = {str(job.get("status")) for job in jobs}
    if statuses & {"queued", "processing", "scheduled", "cancelling"}:
        batch["status"] = "processing"
    elif statuses == {"completed"}:
        batch["status"] = "completed"
    elif statuses:
        batch["status"] = "completed_with_errors"
    return JSONResponse(batch)


@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job_endpoint(job_id: str) -> JSONResponse:
    if JOB_SERVICE.get(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    job = JOB_SERVICE.request_cancel(job_id)
    return JSONResponse(job_response(job_id, job))


@app.post("/api/v1/jobs/{job_id}/retry")
def retry_job_endpoint(job_id: str) -> JSONResponse:
    source = JOB_SERVICE.get(job_id)
    if source is None:
        raise HTTPException(status_code=404, detail="job not found")
    resume_request = source.get("resume_request")
    if not isinstance(resume_request, dict):
        raise HTTPException(status_code=409, detail="job không có cấu hình retry")
    video_path = Path(str(resume_request.get("video_path", "")))
    source_url = resume_request.get("source_url")
    if not video_path.is_file() and not source_url:
        raise HTTPException(status_code=409, detail="file video nguồn không còn tồn tại")
    new_job_id = uuid.uuid4().hex[:8]
    retry_request = dict(resume_request)
    source_cookie_value = retry_request.get("source_cookie_path")
    if source_cookie_value:
        source_cookie_path = Path(str(source_cookie_value)).resolve()
        if source_cookie_path.parent == UPLOAD_DIR.resolve() and source_cookie_path.is_file():
            retry_cookie_path = UPLOAD_DIR / f"{new_job_id}.cookies.txt"
            shutil.copyfile(source_cookie_path, retry_cookie_path)
            try:
                retry_cookie_path.chmod(0o600)
            except OSError:
                logger.warning("Unable to restrict retry cookie file permissions for job %s", new_job_id)
            retry_request["source_cookie_path"] = str(retry_cookie_path)
    new_payload = {
        key: value
        for key, value in source.items()
        if key
        in {
            "mode",
            "filename",
            "source_url",
            "source_platform",
            "subtitle_source",
            "voice_routing",
            "tiktok",
            "ocr_config",
        }
    }
    new_payload.update(
        {
            "status": "queued",
            "step": "queued",
            "progress": 0.0,
            "resume_request": retry_request,
            "retry_of": job_id,
        }
    )
    JOB_SERVICE.create(new_job_id, new_payload)
    batch_id = source.get("batch_id")
    if batch_id:
        JOB_SERVICE.attach_to_batch(new_job_id, str(batch_id))
    notify_scheduler()
    return JSONResponse(job_response(new_job_id, new_payload), status_code=201)


@app.delete("/api/v1/jobs/{job_id}")
def delete_job_endpoint(job_id: str) -> JSONResponse:
    job = JOB_SERVICE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.get("status") in {"queued", "processing", "scheduled", "cancelling"}:
        raise HTTPException(status_code=409, detail="Hãy hủy và chờ job dừng trước khi xóa")
    for key in ("output_video", "subtitle_file", "translation_file", "tiktok_json_file", "tiktok_text_file"):
        filename = job.get(key)
        if filename:
            target = (OUTPUT_DIR / str(filename)).resolve()
            if target.parent == OUTPUT_DIR.resolve():
                target.unlink(missing_ok=True)
    (OUTPUT_DIR / f"{job_id}.artifacts.zip").unlink(missing_ok=True)
    resume_request = job.get("resume_request")
    if isinstance(resume_request, dict):
        for source_key in ("video_path", "source_cookie_path"):
            source_value = resume_request.get(source_key)
            if not source_value:
                continue
            source_path = Path(str(source_value)).resolve()
            if source_path.parent == UPLOAD_DIR.resolve():
                source_path.unlink(missing_ok=True)
    job_work_dir = (WORK_DIR / job_id).resolve()
    if job_work_dir.parent == WORK_DIR.resolve() and job_work_dir.exists():
        shutil.rmtree(job_work_dir)
    JOB_SERVICE.delete(job_id)
    return JSONResponse({"job_id": job_id, "deleted": True})


@app.get("/status/{job_id}")
def get_status(job_id: str) -> JSONResponse:
    job = JOB_SERVICE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(job_response(job_id, job, JOB_SERVICE.queue_positions()))


@app.get("/download/{filename}")
def download(filename: str) -> FileResponse:
    target = (OUTPUT_DIR / Path(filename).name).resolve()
    if target.parent != OUTPUT_DIR.resolve() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target)


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    security_schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    security_schemes["OAuth2PasswordBearer"] = {
        "type": "oauth2",
        "flows": {
            "password": {
                "tokenUrl": "/api/v1/auth/token",
                "scopes": {"admin": "Quản trị toàn bộ pipeline VidTrans"},
            }
        },
    }
    for path, path_item in schema.get("paths", {}).items():
        if not is_protected_path(path):
            continue
        for method, operation in path_item.items():
            if method.lower() in {"get", "post", "put", "patch", "delete", "options", "head"}:
                operation["security"] = [{"OAuth2PasswordBearer": ["admin"]}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
