import asyncio
import concurrent.futures
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, quote_plus, urlsplit

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, ValidationError
from infrastructure.frontend_static import FrontendStaticFiles
from gtts import gTTS
from starlette.background import BackgroundTask
from infrastructure.artifact_archive import build_artifact_archive

from app.config import AppSettings
from application.job_scheduler import JobScheduler
from application.job_service import JobService
from application.comic_routes import ComicRouteDependencies, create_comic_router, requeue_comic_job
from application.comic_service import COMIC_JOB_ACTION, ComicJobRunner, is_comic_job
from application.instagram_routes import create_instagram_router
from application.seo_routes import create_seo_router
from application.threads_routes import create_threads_router
from application.social_publish_routes import create_social_publish_router
from application.social_account_routes import create_social_account_router
from application.social_connection_routes import create_social_connection_router
from application.social_publishing import SocialPublishScheduler, SocialPublishingService
from application.tiktok_browser_routes import create_tiktok_browser_router
from infrastructure.tiktok_browser import TikTokBrowserManager
from domain.models import ProcessingMode, ProcessingRequest
from domain.languages import language_profile, languages_from_config, normalize_language, voice_preset

from infrastructure.douyin_browser_auth import DouyinBrowserAuthManager
from infrastructure.social_browser_auth import SocialBrowserAuthManager
from infrastructure.auth import (
    AuthConfigurationError,
    AuthManager,
    InvalidTokenError,
    LoginRateLimitError,
    hash_password,
)
from infrastructure.admin_accounts import AdminAccountError, AdminAccountStore
from infrastructure.database import database_target
from infrastructure.payments import (
    PLAN_CATALOG,
    PaymentConfig,
    PaymentError,
    PaymentGatewayClient,
    PaymentOrderStore,
)
from infrastructure.job_events import ALL_JOBS, JobEventHub
from infrastructure.job_store import JobNotFoundError, JobStateConflictError, JobStore
from infrastructure.publishers.registry import PublisherRegistry
from infrastructure.publishers.instagram import InstagramPublisher, InstagramPublisherConfig
from infrastructure.publishers.tiktok import TikTokSocialPublisher
from infrastructure.publishers.threads import ThreadsPublisher
from infrastructure.publishers.pinterest import PinterestPublisher
from infrastructure.publishers.linkedin import LinkedInPublisher
from infrastructure.publishers.facebook import FacebookPublisher, FacebookPublisherConfig, FacebookPublisherError
from infrastructure.publishers.youtube import YouTubePublisher, YouTubePublisherConfig, YouTubePublisherError
from infrastructure.platform_apps import PLATFORM_APPS, PlatformAppError, PlatformAppStore, load_or_create_key
from infrastructure.publishers.encrypted_oauth_store import EncryptedOAuthAccountStore
from infrastructure.credential_cipher import CredentialCipher, CredentialCipherError
from infrastructure.signed_media import SignedMediaUrlService
from infrastructure.social_publish_store import SocialPublishStore
from infrastructure.social_credentials import SocialCredentialStore, is_app_redirect_uri
from infrastructure.tenant_governance import QuotaExceededError, TenantGovernanceStore
from infrastructure.media_validation import InvalidVideoError, public_error_message, validate_video_file
from infrastructure.artifact_io import copy_file_atomic, write_json_atomic
from infrastructure.mailer import MailConfig, MailDeliveryError, SmtpMailer
from infrastructure.media_probe import MediaProbe, probe_media_file
from infrastructure.password_reset import CODE_TTL_SECONDS, PasswordResetStore
from infrastructure.stage_checkpoint import (
    StageCheckpointStore,
    fingerprint_payload,
    load_or_compute_json,
    sha256_file,
)
from infrastructure.workflow_events import (
    N8nWebhookClient,
    N8nWebhookConfig,
    N8nWorkflowDispatcher,
    WorkflowEventSink,
    WorkflowEventStore,
)
from infrastructure.social_video_downloader import (
    SocialVideoDownloadCancelled,
    SocialVideoDownloader,
    SocialVideoDownloadError,
    extract_social_video_urls,
    social_platform,
)
from infrastructure.tts_providers import (
    default_tts_provider,
    ensure_provider_configured,
    normalize_tts_provider,
    VOICE_PREVIEW_TEXT,
    audio_content_type,
    cit_voice_preview,
    elevenlabs_voice_preview,
    provider_statuses,
    resolve_tts_voice_selection,
    synthesize_azure,
    synthesize_cit,
    synthesize_elevenlabs,
    tts_account_blocked,
)
from domain.plan_entitlements import (
    PlanEntitlementError,
    annotate_providers,
    ensure_comic_settings_allowed,
    ensure_video_providers_allowed,
)
from infrastructure.translation_providers import (
    create_translation_provider,
    default_translation_provider,
    ensure_translation_provider_configured,
    ollama_url,
    translation_provider_statuses,
)
from infrastructure.system_events import SystemMetricsHub
from infrastructure.system_metrics import HostProcessCollector, SystemMetricsCollector
from infrastructure.tiktok_publisher import TikTokPublisher, TikTokPublisherConfig, TikTokPublisherError
from pipeline.ocr import (
    OCRConfig,
    annotate_ocr_segments_with_asr,
    extract_burned_subtitle_segments,
    merge_independent_ocr_asr_segments,
    merge_ocr_and_asr_segments,
)
from pipeline.comic_video import ComicRuntime
from pipeline.checkpoint_payloads import (
    restore_file_checkpoint,
    restore_tts_checkpoint,
    serialize_tts_checkpoint,
    valid_file_checkpoint,
    valid_transcript_checkpoint,
    valid_translation_checkpoint,
    valid_tts_checkpoint,
)
from pipeline.parallel import collect_indexed_futures
from pipeline.stage_metrics import StageMeasurement, measure_stage
from pipeline.subtitle_layout import SubtitleLayoutOptions, apply_subtitle_layout
from pipeline.subtitle_segmentation import SubtitleSegmentationOptions, merge_fragment_cues, segment_subtitle_cues
from pipeline.caption_alignment import CaptionAlignmentConfig, align_speech_to_burned_captions
from pipeline.subtitle_timing import subtitle_render_window
from pipeline.tiktok import LocalExtractiveTikTokProvider, TikTokPost, write_tiktok_artifacts
from pipeline.asr import ASRConfig, ASRService, resolve_cpu_threads
from pipeline.translation import (
    ensure_translation_complete,
    filter_meaningful_segments,
    segment_filter_signature,
    translate_segments,
    translate_segments_by_source_language,
)
from pipeline.tts_timing import (
    adaptive_synthesis_rate_percent,
    available_voice_window,
    bounded_tempo,
    relaxed_synthesis_rate_percent,
    schedule_voice_segments,
    speech_level_gain,
)
from pipeline.voice_routing import read_pcm_wav, route_segments_by_pitch, route_segments_manually
from pipeline.voice_style import AUTO_VOICE_STYLE, infer_speaker_voice, infer_voice_style

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
DATABASE_TARGET = database_target(WORK_DIR / "jobs.sqlite3")
FFMPEG = SETTINGS.ffmpeg
FFPROBE = SETTINGS.ffprobe


def storage_owner_segment(owner_user_id: str) -> str:
    """Return a filesystem-safe, stable tenant directory name."""
    value = str(owner_user_id).strip()
    if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def tenant_job_dir(root: Path, owner_user_id: str, job_id: str) -> Path:
    path = (root / "users" / storage_owner_segment(owner_user_id) / job_id).resolve()
    root_resolved = root.resolve()
    if not path.is_relative_to(root_resolved):
        raise ValueError("Invalid tenant storage path")
    path.mkdir(parents=True, exist_ok=True)
    return path


def tenant_storage_path(root: Path, owner_user_id: str, resource_id: str) -> Path:
    """Resolve an existing tenant path without creating it."""
    path = (root / "users" / storage_owner_segment(owner_user_id) / resource_id).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Invalid tenant storage path")
    return path


def stored_artifact_path(filename: str) -> Path:
    path = (OUTPUT_DIR / str(filename)).resolve()
    if not path.is_relative_to(OUTPUT_DIR.resolve()):
        raise ValueError("Invalid artifact path")
    return path


def stored_artifact_name(path: Path) -> str:
    return path.resolve().relative_to(OUTPUT_DIR.resolve()).as_posix()

VOICE_OPTIONS = {
    "female": "vi-VN-HoaiMyNeural",
    "male": "vi-VN-NamMinhNeural",
}
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
DEFAULT_SUB_STYLE = {
    # DejaVu Sans is installed in the Docker image and includes Vietnamese
    # diacritics. Noto Sans CJK is retained as a fallback for any source text
    # that cannot be translated, but must not be forced as the render font.
    "font_name": "DejaVu Sans",
    "font_size": 36,
    "primary_color": "&H00FFFFFF",
    "outline_color": "&H00000000",
    "back_color": "&H2E000000",
    "outline": 5,
    "shadow": 0,
    "border_style": 3,
    "alignment": 2,
    "margin_v": 60,
    "placement_mode": "replace_original",
    "match_source_size": True,
    "min_font_size": 22,
    "max_font_size": 72,
    "position_gap": 14,
    "mask_original": True,
    "preferred_cue_chars": 14,
    "max_cue_chars": 20,
    "cue_pause_seconds": 0.65,
    "max_cue_duration_seconds": 3.2,
    "text_color_css": "#FFFFFF",
    "background_color_css": "#000000",
    "background_opacity": 0.82,
    "background_box": True,
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
DEFAULT_VOICE_MIX_GAIN = 1.30
DEFAULT_ORIGINAL_AUDIO_VOLUME = 0.60
# Original audio is ducked under the dub so it stays audible (emotion, tone)
# in pauses without masking the translated voice.
ORIGINAL_DUCK_FILTER = "sidechaincompress=threshold=0.05:ratio=6:attack=15:release=400:knee=4"
ORIGINAL_DUCK_EQ = "equalizer=f=2500:t=q:w=1.2:g=-5"


def _validated_css_color(value: Any, fallback: str) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if re.fullmatch(r"#[0-9A-F]{6}", candidate) else fallback


def _css_color_to_ass(value: str, opacity: float = 1.0) -> str:
    color = _validated_css_color(value, "#FFFFFF")
    opacity = max(0.0, min(1.0, float(opacity)))
    red, green, blue = color[1:3], color[3:5], color[5:7]
    alpha = round((1.0 - opacity) * 255)
    return f"&H{alpha:02X}{blue}{green}{red}"


def normalize_subtitle_style(style: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(style)
    text_color = _validated_css_color(style.get("text_color_css"), "#FFFFFF")
    background_color = _validated_css_color(style.get("background_color_css"), "#000000")
    background_opacity = max(0.0, min(1.0, float(style.get("background_opacity", 0.82))))
    fallback_background_style = "box" if bool(style.get("background_box", True)) else "none"
    background_style = str(style.get("background_style", fallback_background_style))
    if background_style not in {"box", "blurred_bottom", "none"}:
        background_style = fallback_background_style
    background_box = background_style == "box"
    normalized.update(
        text_color_css=text_color,
        background_color_css=background_color,
        background_opacity=background_opacity,
        background_box=background_box,
        background_style=background_style,
        primary_color=_css_color_to_ass(text_color),
        back_color=_css_color_to_ass(background_color, background_opacity),
        border_style=3 if background_box else 1,
        outline=5 if background_box else int(style.get("outline", 3)),
    )
    return normalized

app = FastAPI(title="VidTrans", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# job_change_snapshot is defined with the job routes below; it is only
# called once the broadcaster runs, after the module has loaded.
JOB_EVENTS = JobEventHub(lambda job_ids: job_change_snapshot(job_ids))
JOB_SERVICE = JobService(JobStore(DATABASE_TARGET), on_change=JOB_EVENTS.notify)
N8N_WORKFLOW_CONFIG = N8nWebhookConfig.from_env()
WORKFLOW_EVENT_STORE = WorkflowEventStore(DATABASE_TARGET)
WORKFLOW_EVENTS = WorkflowEventSink(WORKFLOW_EVENT_STORE)
N8N_WORKFLOW_DISPATCHER = N8nWorkflowDispatcher(
    WORKFLOW_EVENT_STORE,
    N8nWebhookClient(N8N_WORKFLOW_CONFIG),
)
TIKTOK_SUMMARY_PROVIDER = LocalExtractiveTikTokProvider()
_social_cookie_value = os.environ.get("VIDTRANS_YTDLP_COOKIE_FILE", "").strip()
DOUYIN_BROWSER_AUTH = DouyinBrowserAuthManager(WORK_DIR / "douyin-auth" / "douyin.cookies.txt")
YOUTUBE_BROWSER_AUTH = SocialBrowserAuthManager(
    "youtube", WORK_DIR / "youtube-browser-auth" / "youtube.cookies.txt"
)
FACEBOOK_BROWSER_AUTH = SocialBrowserAuthManager(
    "facebook", WORK_DIR / "facebook-browser-auth" / "facebook.cookies.txt"
)
SOCIAL_VIDEO_DOWNLOADER = SocialVideoDownloader(
    ffmpeg_location=FFMPEG,
    cookie_file=Path(_social_cookie_value) if _social_cookie_value else None,
    douyin_cookie_provider=DOUYIN_BROWSER_AUTH.export_download_cookies,
    platform_cookie_providers={
        "YouTube": YOUTUBE_BROWSER_AUTH.export_download_cookies,
        "Facebook": FACEBOOK_BROWSER_AUTH.export_download_cookies,
    },
    platform_user_agent_providers={
        "YouTube": YOUTUBE_BROWSER_AUTH.browser_user_agent,
        "Facebook": FACEBOOK_BROWSER_AUTH.browser_user_agent,
    },
    douyin_resolver=DOUYIN_BROWSER_AUTH.resolve_video,
    facebook_resolver=FACEBOOK_BROWSER_AUTH.resolve_facebook_video,
    tiktok_proxy=os.environ.get("VIDTRANS_TIKTOK_PROXY", "").strip() or None,
    douyin_proxy=os.environ.get("VIDTRANS_DOUYIN_PROXY", "").strip() or None,
    youtube_proxy=os.environ.get("VIDTRANS_YOUTUBE_PROXY", "").strip() or None,
)
# Each user may enter their own OAuth developer apps on the web; .env stays the fallback.
PLATFORM_APP_STORE = PlatformAppStore(
    DATABASE_TARGET,
    CredentialCipher(load_or_create_key(WORK_DIR / "secrets" / "platform-apps.key"), key_id="platform-apps"),
)
_PLATFORM_CONFIG_FACTORIES = {
    "TIKTOK": TikTokPublisherConfig.from_env,
    "FACEBOOK": FacebookPublisherConfig.from_env,
    "INSTAGRAM": InstagramPublisherConfig.from_env,
    "YOUTUBE": YouTubePublisherConfig.from_env,
}


def platform_app_config(platform: str, user_id: str | None = None):
    key = platform.upper()
    config = _PLATFORM_CONFIG_FACTORIES[key]()
    return PLATFORM_APP_STORE.overlay(user_id, key, config) if user_id else config


_TENANT_PUBLISHER_LOCK = threading.RLock()
_TENANT_TIKTOK_PUBLISHERS: dict[str, TikTokPublisher] = {}
_TENANT_SOCIAL_REGISTRIES: dict[str, PublisherRegistry] = {}


def job_owner_user_id(job_id: str) -> str:
    job = JOB_SERVICE.get(job_id) or {}
    owner = str(job.get("owner_user_id") or "")
    if not owner:
        raise RuntimeError("Job không có owner")
    return owner


def tenant_social_auth_dir(owner_user_id: str, platform: str) -> Path:
    path = (WORK_DIR / "users" / storage_owner_segment(owner_user_id) / "social-auth" / platform).resolve()
    if not path.is_relative_to(WORK_DIR.resolve()):
        raise RuntimeError("Invalid social credential path")
    path.mkdir(parents=True, exist_ok=True)
    return path


def tenant_tiktok_publisher(owner_user_id: str) -> TikTokPublisher:
    with _TENANT_PUBLISHER_LOCK:
        publisher = _TENANT_TIKTOK_PUBLISHERS.get(owner_user_id)
        if publisher is None:
            publisher = TikTokPublisher(
                tenant_social_auth_dir(owner_user_id, "tiktok"),
                config=platform_app_config("TIKTOK", owner_user_id),
                account_store=tenant_encrypted_oauth_store(owner_user_id, "TIKTOK"),
            )
            _TENANT_TIKTOK_PUBLISHERS[owner_user_id] = publisher
        return publisher


def tenant_social_registry(owner_user_id: str) -> PublisherRegistry:
    with _TENANT_PUBLISHER_LOCK:
        registry = _TENANT_SOCIAL_REGISTRIES.get(owner_user_id)
        if registry is None:
            tiktok = tenant_tiktok_publisher(owner_user_id)
            registry = PublisherRegistry([
                InstagramPublisher(
                    tenant_social_auth_dir(owner_user_id, "instagram"),
                    SIGNED_MEDIA_URLS,
                    config=platform_app_config("INSTAGRAM", owner_user_id),
                    account_store=tenant_encrypted_oauth_store(owner_user_id, "INSTAGRAM"),
                ),
                ThreadsPublisher(tenant_social_auth_dir(owner_user_id, "threads"), SIGNED_MEDIA_URLS),
                PINTEREST_PUBLISHER,
                LINKEDIN_PUBLISHER,
                FacebookPublisher(
                    tenant_social_auth_dir(owner_user_id, "facebook"),
                    config=platform_app_config("FACEBOOK", owner_user_id),
                    account_store=tenant_encrypted_oauth_store(owner_user_id, "FACEBOOK"),
                ),
                YouTubePublisher(
                    tenant_social_auth_dir(owner_user_id, "youtube"),
                    config=platform_app_config("YOUTUBE", owner_user_id),
                    account_store=tenant_encrypted_oauth_store(owner_user_id, "YOUTUBE"),
                ),
                TikTokSocialPublisher(tiktok),
            ])
            _TENANT_SOCIAL_REGISTRIES[owner_user_id] = registry
        return registry


TIKTOK_PUBLISHER = TikTokPublisher(WORK_DIR / "tiktok-auth", config=platform_app_config("TIKTOK"))
TIKTOK_BROWSER = TikTokBrowserManager(WORK_DIR / "tiktok-browser")
app.include_router(create_tiktok_browser_router(
    TIKTOK_BROWSER,
    JOB_SERVICE,
    OUTPUT_DIR,
    TIKTOK_PUBLISHER,
    owner_resolver=lambda request: str(current_account(request)["user_id"]),
    publisher_resolver=lambda request: tenant_tiktok_publisher(str(current_account(request)["user_id"])),
    can_manage_all_resolver=lambda request: str(current_account(request).get("role") or "") == "owner",
))
SOCIAL_PUBLISH_STORE = SocialPublishStore(DATABASE_TARGET)
SIGNED_MEDIA_URLS = SignedMediaUrlService(
    OUTPUT_DIR,
    public_base_url=os.environ.get("VIDTRANS_PUBLIC_BASE_URL", "").strip(),
    secret=os.environ.get("VIDTRANS_MEDIA_SIGNING_SECRET", "").strip(),
)
INSTAGRAM_PUBLISHER = InstagramPublisher(
    WORK_DIR / "instagram-auth",
    SIGNED_MEDIA_URLS,
    config=platform_app_config("INSTAGRAM"),
)
THREADS_PUBLISHER = ThreadsPublisher(WORK_DIR / "threads-auth", SIGNED_MEDIA_URLS)
PINTEREST_PUBLISHER = PinterestPublisher()
LINKEDIN_PUBLISHER = LinkedInPublisher()
FACEBOOK_PUBLISHER = FacebookPublisher(WORK_DIR / "facebook-auth", config=platform_app_config("FACEBOOK"))
YOUTUBE_PUBLISHER = YouTubePublisher(WORK_DIR / "youtube-auth", config=platform_app_config("YOUTUBE"))
TIKTOK_SOCIAL_PUBLISHER = TikTokSocialPublisher(TIKTOK_PUBLISHER)
SOCIAL_PUBLISHERS = PublisherRegistry([
    INSTAGRAM_PUBLISHER, THREADS_PUBLISHER, PINTEREST_PUBLISHER, LINKEDIN_PUBLISHER,
    FACEBOOK_PUBLISHER, YOUTUBE_PUBLISHER,
    TIKTOK_SOCIAL_PUBLISHER,
])
SOCIAL_PUBLISHING = SocialPublishingService(
    SOCIAL_PUBLISH_STORE,
    SOCIAL_PUBLISHERS,
    publisher_resolver=lambda platform, job_id: tenant_social_registry(job_owner_user_id(job_id)).get(platform),
)
app.include_router(create_social_publish_router(
    SOCIAL_PUBLISHING,
    JOB_SERVICE,
    OUTPUT_DIR,
    owner_resolver=lambda request: str(current_account(request)["user_id"]),
    connection_resolver=lambda user_id, connection_id, platform: resolve_social_connection(
        user_id, connection_id, platform
    ),
    can_manage_all_resolver=lambda request: str(current_account(request).get("role") or "") == "owner",
))
app.include_router(create_instagram_router(
    INSTAGRAM_PUBLISHER, SIGNED_MEDIA_URLS,
    publisher_resolver=lambda request: tenant_publisher_for_request(request, "INSTAGRAM"),
    oauth_session_registrar=lambda request, platform, url: register_tenant_oauth_session(request, platform, url),
    callback_publisher_resolver=lambda platform, state: tenant_publisher_for_oauth_callback(platform, state),
))
app.include_router(create_threads_router(
    THREADS_PUBLISHER,
    publisher_resolver=lambda request: tenant_publisher_for_request(request, "THREADS"),
    oauth_session_registrar=lambda request, platform, url: register_tenant_oauth_session(request, platform, url),
    callback_publisher_resolver=lambda platform, state: tenant_publisher_for_oauth_callback(platform, state),
))
app.include_router(create_social_account_router(
    "facebook", FACEBOOK_PUBLISHER, FacebookPublisherError,
    publisher_resolver=lambda request: tenant_publisher_for_request(request, "FACEBOOK"),
    oauth_session_registrar=lambda request, platform, url: register_tenant_oauth_session(request, platform, url),
    callback_publisher_resolver=lambda platform, state: tenant_publisher_for_oauth_callback(platform, state),
))
app.include_router(create_social_account_router(
    "youtube", YOUTUBE_PUBLISHER, YouTubePublisherError,
    publisher_resolver=lambda request: tenant_publisher_for_request(request, "YOUTUBE"),
    oauth_session_registrar=lambda request, platform, url: register_tenant_oauth_session(request, platform, url),
    callback_publisher_resolver=lambda platform, state: tenant_publisher_for_oauth_callback(platform, state),
))
AUTH_MANAGER = AuthManager()
ADMIN_ACCOUNTS = AdminAccountStore(
    DATABASE_TARGET,
    AUTH_MANAGER.config.username if AUTH_MANAGER.config.enabled else "admin",
    AUTH_MANAGER.config.password_hash if AUTH_MANAGER.config.enabled else "",
)
_bootstrap_login = AUTH_MANAGER.config.username or "admin"
_bootstrap_email = _bootstrap_login if "@" in _bootstrap_login else f"{_bootstrap_login}@local.vidtrans"
_bootstrap_owner = ADMIN_ACCOUNTS.get_by_email(_bootstrap_email)
if _bootstrap_owner is None:
    raise RuntimeError("Bootstrap owner account is missing")
JOB_SERVICE.backfill_legacy_owner(str(_bootstrap_owner["user_id"]))
MAILER = SmtpMailer(MailConfig.from_env())
PASSWORD_RESETS = PasswordResetStore(DATABASE_TARGET, AUTH_MANAGER.config.jwt_secret)


def copy_legacy_file(source: Path, destination: Path) -> bool:
    if not source.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    return destination.is_file() and destination.stat().st_size == source.stat().st_size


def migrate_legacy_job_files() -> dict[str, Any]:
    """Namespace legacy files without deleting the recoverable originals."""
    report: dict[str, Any] = {
        "jobs_scanned": 0,
        "jobs_updated": 0,
        "files_copied": 0,
        "files_missing": [],
        "work_directories_copied": 0,
    }
    offset = 0
    while True:
        jobs, total = JOB_SERVICE.list_jobs(limit=200, offset=offset)
        if not jobs:
            break
        for job in jobs:
            report["jobs_scanned"] += 1
            job_id = str(job["job_id"])
            owner_user_id = str(job.get("owner_user_id") or _bootstrap_owner["user_id"])
            updates: dict[str, Any] = {}
            output_dir = tenant_job_dir(OUTPUT_DIR, owner_user_id, job_id)
            for field in ("output_video", "subtitle_file", "translation_file", "tiktok_json_file", "tiktok_text_file"):
                value = str(job.get(field) or "")
                if not value or "/" in value or "\\" in value:
                    continue
                source = (OUTPUT_DIR / value).resolve()
                destination = output_dir / Path(value).name
                if copy_legacy_file(source, destination):
                    updates[field] = stored_artifact_name(destination)
                    report["files_copied"] += 1
                elif not source.is_file():
                    report["files_missing"].append({"job_id": job_id, "field": field, "name": Path(value).name})
            resume_request = job.get("resume_request")
            if isinstance(resume_request, dict):
                migrated_request = dict(resume_request)
                request_changed = False
                upload_dir = tenant_job_dir(UPLOAD_DIR, owner_user_id, job_id)
                for field in ("video_path", "source_cookie_path", "background_music_path"):
                    raw_path = str(migrated_request.get(field) or "")
                    if not raw_path:
                        continue
                    source = Path(raw_path).resolve()
                    if not source.is_relative_to(UPLOAD_DIR.resolve()) or source.is_relative_to(upload_dir):
                        continue
                    destination = upload_dir / source.name
                    if copy_legacy_file(source, destination):
                        migrated_request[field] = str(destination)
                        request_changed = True
                        report["files_copied"] += 1
                    elif not source.is_file():
                        report["files_missing"].append({"job_id": job_id, "field": field, "name": source.name})
                if request_changed:
                    updates["resume_request"] = migrated_request
            legacy_work = (WORK_DIR / job_id).resolve()
            tenant_work = tenant_job_dir(WORK_DIR, owner_user_id, job_id)
            if legacy_work.is_dir() and legacy_work != tenant_work:
                shutil.copytree(legacy_work, tenant_work, dirs_exist_ok=True)
                report["work_directories_copied"] += 1
            if updates:
                JOB_SERVICE.update(job_id, **updates)
                report["jobs_updated"] += 1
        offset += len(jobs)
        if offset >= total:
            break
    report["files_missing_count"] = len(report["files_missing"])
    return report


_legacy_job_migration = migrate_legacy_job_files()
if _legacy_job_migration["jobs_updated"]:
    logger.info(
        "Namespaced %d legacy jobs and %d files",
        _legacy_job_migration["jobs_updated"], _legacy_job_migration["files_copied"],
    )


def migrate_legacy_social_auth(owner_user_id: str) -> int:
    """Copy pre-tenant OAuth files to the bootstrap owner without overwriting."""
    migrated = 0
    for platform in ("tiktok", "instagram", "threads", "facebook", "youtube"):
        source = WORK_DIR / f"{platform}-auth"
        destination = tenant_social_auth_dir(owner_user_id, platform)
        if not source.is_dir():
            continue
        for source_file in source.rglob("*"):
            if not source_file.is_file():
                continue
            relative = source_file.relative_to(source)
            target = destination / relative
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)
            migrated += 1
    return migrated


_migrated_social_auth_files = migrate_legacy_social_auth(str(_bootstrap_owner["user_id"]))
if _migrated_social_auth_files:
    logger.info("Migrated %d legacy social credential files to bootstrap owner", _migrated_social_auth_files)

_tenant_migration_manifest = {
    "schema_version": "tenant-storage-v1",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "bootstrap_owner_user_id": str(_bootstrap_owner["user_id"]),
    "status": "attention" if _legacy_job_migration["files_missing_count"] else "complete",
    "job_storage": _legacy_job_migration,
    "social_auth_files_copied": _migrated_social_auth_files,
    "legacy_sources_preserved": True,
}
_tenant_manifest_path = WORK_DIR / "migrations" / "tenant-storage-v1.json"
write_json_atomic(_tenant_manifest_path, _tenant_migration_manifest)
SOCIAL_CREDENTIALS = SocialCredentialStore(DATABASE_TARGET)
try:
    SOCIAL_CREDENTIAL_CIPHER: CredentialCipher | None = CredentialCipher.from_env()
except CredentialCipherError:
    SOCIAL_CREDENTIAL_CIPHER = None
    logger.warning(
        "Encrypted social connection storage is disabled; set "
        "VIDTRANS_CREDENTIAL_ENCRYPTION_KEY before enabling self-service OAuth"
    )


def tenant_encrypted_oauth_store(owner_user_id: str, platform: str):
    if SOCIAL_CREDENTIAL_CIPHER is None:
        return None
    return EncryptedOAuthAccountStore(
        SOCIAL_CREDENTIALS,
        SOCIAL_CREDENTIAL_CIPHER,
        user_id=owner_user_id,
        platform=platform,
    )


def resolve_social_connection(user_id: str, connection_id: str, platform: str) -> dict[str, Any]:
    connection = SOCIAL_CREDENTIALS.get_for_user(
        user_id=user_id, connection_id=connection_id
    )
    if str(connection.get("status")) != "active":
        raise ValueError("Kết nối mạng xã hội không còn hoạt động")
    if str(connection.get("platform")) != platform.strip().upper():
        raise ValueError("Kết nối không thuộc nền tảng đã chọn")
    return connection


app.include_router(create_social_connection_router(
    SOCIAL_CREDENTIALS,
    owner_resolver=lambda request: str(current_account(request)["user_id"]),
))


def tenant_publisher_for_request(request: Request, platform: str):
    user_id = str(current_account(request)["user_id"])
    if platform.upper() == "TIKTOK":
        return tenant_tiktok_publisher(user_id)
    return tenant_social_registry(user_id).get(platform.upper())


def app_redirect_allowed(uri: str) -> bool:
    """Mobile deep links the OAuth callbacks may return to (VIDTRANS_APP_REDIRECT_URIS)."""
    allowed = {
        item.strip() for item in re.split(r"[,;\s]+", os.environ.get("VIDTRANS_APP_REDIRECT_URIS", "")) if item.strip()
    }
    base = uri.split("#", 1)[0].split("?", 1)[0]
    return is_app_redirect_uri(uri) and base in allowed


def register_tenant_oauth_session(request: Request, platform: str, authorization_url: str) -> None:
    state = (parse_qs(urlsplit(authorization_url).query).get("state") or [""])[0]
    if not state:
        raise HTTPException(status_code=503, detail="Nhà cung cấp OAuth không trả về state")
    # Apps start OAuth with ?return_to=<deep link>; the callback then returns there instead of the web UI.
    return_to = request.query_params.get("return_to", "").strip()
    if return_to and not app_redirect_allowed(return_to):
        raise HTTPException(status_code=400, detail="return_to không nằm trong VIDTRANS_APP_REDIRECT_URIS")
    SOCIAL_CREDENTIALS.create_oauth_session(
        user_id=str(current_account(request)["user_id"]),
        platform=platform,
        state=state,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        return_path=return_to or "/#tiktok",
    )


OAUTH_CALLBACK_PATH = re.compile(r"^/api/v1/([a-z]+)-auth/callback$")


@app.middleware("http")
async def oauth_app_redirect(request: Request, call_next):
    """Send an OAuth callback started from the mobile app back to the app's deep link.

    Every platform's callback redirects to the web UI with ?<platform>=connected or
    ?<platform>_error=...; for app-started sessions that query is moved onto the deep link.
    """
    match = OAUTH_CALLBACK_PATH.match(request.url.path)
    state = request.query_params.get("state", "") if match else ""
    return_to = (
        await asyncio.to_thread(SOCIAL_CREDENTIALS.oauth_return_path, platform=match[1], state=state)
        if state else None
    )
    response = await call_next(request)
    if return_to and response.status_code in (302, 303, 307) and app_redirect_allowed(return_to):
        query = urlsplit(response.headers.get("location", "")).query
        base, _, fragment = return_to.partition("#")
        separator = "&" if "?" in base else "?"
        target = f"{base}{separator}{query}" if query else base
        response.headers["location"] = f"{target}#{fragment}" if fragment else target
    return response


def tenant_publisher_for_oauth_callback(platform: str, state: str):
    session = SOCIAL_CREDENTIALS.consume_oauth_session(platform=platform, state=state)
    if session is None:
        raise ValueError("Phiên kết nối mạng xã hội không hợp lệ hoặc đã hết hạn")
    user_id = str(session["user_id"])
    if platform.upper() == "TIKTOK":
        publisher = tenant_tiktok_publisher(user_id)
        publisher.accept_consumed_state(state)
        return publisher
    publisher = tenant_social_registry(user_id).get(platform.upper())
    if hasattr(publisher, "accept_consumed_state"):
        publisher.accept_consumed_state(state)
    account_store = getattr(publisher, "store", None)
    if account_store is not None and hasattr(account_store, "accept_consumed_state"):
        account_store.accept_consumed_state(state)
    return publisher
PAYMENT_CONFIG = PaymentConfig.from_env()
PAYMENT_ORDERS = PaymentOrderStore(DATABASE_TARGET)
PAYMENT_GATEWAY = PaymentGatewayClient(PAYMENT_CONFIG)
TENANT_GOVERNANCE = TenantGovernanceStore(DATABASE_TARGET)
ASR_SERVICE = ASRService(
    ASRConfig.from_env(),
    cpu_threads=resolve_cpu_threads(
        int(os.environ.get("VIDTRANS_ASR_CPU_THREADS", "0")), SETTINGS.whisper_concurrency
    ),
)
_whisper_slots = threading.BoundedSemaphore(SETTINGS.whisper_concurrency)
_ocr_slots = threading.BoundedSemaphore(SETTINGS.ocr_concurrency)
_job_context = threading.local()
_stage_record_lock = threading.Lock()


TIKTOK_CANCEL_UNCERTAIN_DETAIL = (
    "Đã hủy khi video đang được gửi lên TikTok; TikTok có thể đã nhận video, "
    "hãy kiểm tra và xóa thủ công trên TikTok nếu cần"
)


class JobCancelled(RuntimeError):
    pass


PUBLIC_AUTH_PATHS = {
    "/api/v1/auth/status",
    "/api/v1/auth/token",
    "/api/v1/auth/refresh",
    "/api/v1/auth/register",
    "/api/v1/auth/password-reset/request",
    "/api/v1/auth/password-reset/confirm",
    "/api/v1/tiktok-auth/callback",
    "/api/v1/instagram-auth/callback",
    "/api/v1/threads-auth/callback",
    "/api/v1/facebook-auth/callback",
    "/api/v1/youtube-auth/callback",
    "/api/v1/billing/callbacks/momo",
    "/api/v1/billing/callbacks/zalopay",
    "/api/v1/billing/callbacks/bank",
}
SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_protected_path(path: str) -> bool:
    if path.startswith("/api/v1/social-media/"):
        return False
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
    try:
        account = ADMIN_ACCOUNTS.get(str(request.state.auth_claims["user_id"]))
    except AdminAccountError:
        return unauthorized_response()
    if token_predates_password_change(request.state.auth_claims, account):
        return unauthorized_response("Mật khẩu đã được thay đổi. Hãy đăng nhập lại.")
    if account["approval_status"] != "approved":
        return JSONResponse({"detail": "Tài khoản chưa được phê duyệt hoặc đã bị khóa"}, status_code=403)
    request.state.account = account
    if request.url.path.startswith("/api/v1/admin/") and account["role"] not in {"owner", "admin"}:
        return JSONResponse({"detail": "Bạn không có quyền quản trị"}, status_code=403)
    if (
        account["role"] == "viewer"
        and request.method.upper() not in SAFE_HTTP_METHODS
        and not request.url.path.startswith("/api/v1/billing/")
        and request.url.path != "/api/v1/auth/session"
    ):
        return JSONResponse(
            {"detail": "Tài khoản Người xem không có quyền thay đổi dữ liệu"},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
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


def current_account(request: Request) -> dict[str, Any]:
    account = getattr(request.state, "account", None)
    if account is not None:
        return account
    # Authentication-disabled mode is local development only.
    return _bootstrap_owner


def require_roles(request: Request, *roles: str) -> dict[str, Any]:
    account = current_account(request)
    if account["role"] not in roles:
        raise HTTPException(status_code=403, detail="Bạn không có quyền thực hiện thao tác này")
    return account


def can_manage_all_jobs(account: dict[str, Any]) -> bool:
    return str(account.get("role") or "") in {"owner", "admin"}


def job_for_request(request: Request, job_id: str) -> dict[str, Any] | None:
    account = current_account(request)
    if can_manage_all_jobs(account):
        return JOB_SERVICE.get(job_id)
    return JOB_SERVICE.get_for_user(job_id, str(account["user_id"]))


def add_job_owner_identity(payload: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    owner_user_id = str(job.get("owner_user_id") or "")
    payload["owner_user_id"] = owner_user_id
    payload["owner_email"] = owner_user_id
    payload["owner_display_name"] = owner_user_id
    if owner_user_id:
        try:
            owner = ADMIN_ACCOUNTS.get(owner_user_id)
            payload["owner_email"] = str(owner.get("email") or owner_user_id)
            payload["owner_display_name"] = str(owner.get("display_name") or payload["owner_email"])
        except AdminAccountError:
            pass
    return payload


def account_plan_limit(account: dict[str, Any]) -> int | None:
    plan = PLAN_CATALOG.get(str(account.get("plan") or "free"), PLAN_CATALOG["free"])
    minutes = plan.get("minutes")
    return int(minutes) if minutes is not None else None


def reserve_job_quota(job_id: str, account: dict[str, Any], minutes: int = 1) -> None:
    try:
        TENANT_GOVERNANCE.reserve(
            job_id=job_id,
            user_id=str(account["user_id"]),
            limit_minutes=account_plan_limit(account),
            minutes=minutes,
        )
    except QuotaExceededError as exc:
        raise HTTPException(status_code=402, detail=f"Đã hết quota gói {str(account.get('plan', 'free')).upper()}: {exc}") from exc


def reserve_batch_quota(job_ids: list[str], account: dict[str, Any]) -> None:
    try:
        TENANT_GOVERNANCE.reserve_many(
            job_ids=job_ids,
            user_id=str(account["user_id"]),
            limit_minutes=account_plan_limit(account),
        )
    except QuotaExceededError as exc:
        raise HTTPException(status_code=402, detail=f"Đã hết quota gói {str(account.get('plan', 'free')).upper()}: {exc}") from exc


def job_plan_limit(job_id: str) -> int | None:
    owner_user_id = job_owner_user_id(job_id)
    try:
        account = ADMIN_ACCOUNTS.get(owner_user_id)
    except AdminAccountError:
        return 0
    return account_plan_limit(account)


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


# Linux rejects any single argv entry above 128 KiB with E2BIG ("Argument list too long").
# Long filter graphs (drawtext subtitles, many-segment voice mixes) are moved into files.
FFMPEG_INLINE_FILTER_LIMIT = 16 * 1024
_FFMPEG_FILTER_OPTIONS = {
    "-filter_complex": "filter_complex",
    "-lavfi": "filter_complex",
    "-vf": "filter:v",
    "-af": "filter:a",
    "-filter:v": "filter:v",
    "-filter:a": "filter:a",
}
_ffmpeg_major_version_cache: dict[str, int] = {}


def _ffmpeg_major_version(binary: str) -> int:
    if binary not in _ffmpeg_major_version_cache:
        major = 0
        try:
            output = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=10).stdout
            match = re.search(r"ffmpeg version n?(\d+)\.", output)
            major = int(match.group(1)) if match else 0
        except (OSError, subprocess.SubprocessError):
            pass
        _ffmpeg_major_version_cache[binary] = major
    return _ffmpeg_major_version_cache[binary]


def _externalize_long_ffmpeg_filters(args: list[str]) -> tuple[list[str], list[Path]]:
    """Replace oversized filter arguments with script files so exec() never hits E2BIG."""
    rewritten: list[str] = []
    script_files: list[Path] = []
    index = 0
    while index < len(args):
        option = args[index]
        target = _FFMPEG_FILTER_OPTIONS.get(option)
        if target and index + 1 < len(args) and len(args[index + 1].encode("utf-8")) > FFMPEG_INLINE_FILTER_LIMIT:
            handle = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", suffix=".ffscript", prefix="vidtrans-filter-", delete=False
            )
            with handle:
                handle.write(args[index + 1])
            script_path = Path(handle.name)
            script_files.append(script_path)
            if _ffmpeg_major_version(args[0]) >= 7:
                rewritten.extend([f"-/{target}", str(script_path)])
            elif target == "filter_complex":
                rewritten.extend(["-filter_complex_script", str(script_path)])
            else:
                rewritten.extend([f"-filter_script:{target[-1]}", str(script_path)])
            index += 2
            continue
        rewritten.append(option)
        index += 1
    return rewritten, script_files


def run_ffmpeg(
    args: list[str], *, check: bool = True, job_id: str | None = None
) -> subprocess.CompletedProcess[str]:
    original_args = args
    args, script_files = _externalize_long_ffmpeg_filters(list(args))
    try:
        return _run_ffmpeg_process(args, original_args, check=check, job_id=job_id)
    finally:
        for script_file in script_files:
            script_file.unlink(missing_ok=True)


def _run_ffmpeg_process(
    args: list[str], original_args: list[str], *, check: bool, job_id: str | None
) -> subprocess.CompletedProcess[str]:
    logged = " ".join(args)
    logger.info("Running ffmpeg command: %s", logged if len(logged) < 4000 else logged[:4000] + " …")
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            active_job_id = job_id or getattr(_job_context, "job_id", None)
            if active_job_id and JOB_SERVICE.is_cancel_requested(active_job_id):
                process.terminate()
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise JobCancelled("FFmpeg đã dừng theo yêu cầu hủy")
    result = subprocess.CompletedProcess(original_args, process.returncode, stdout, stderr)
    if check and process.returncode:
        exc = subprocess.CalledProcessError(process.returncode, original_args, stdout, stderr)
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
    if len(lines) <= 2:
        return "\n".join(lines)
    # Never discard overflow words. Segmentation normally keeps cues short;
    # legacy/untimed cues are balanced into two lines as a safe fallback.
    best_index = 1
    best_delta: int | None = None
    for index in range(1, len(words)):
        left = " ".join(words[:index])
        right = " ".join(words[index:])
        delta = abs(len(left) - len(right))
        if best_delta is None or delta < best_delta:
            best_index = index
            best_delta = delta
    return " ".join(words[:best_index]) + "\n" + " ".join(words[best_index:])


def find_font_file(preferred_name: str) -> Optional[str]:
    fc_match = shutil.which("fc-match")
    if fc_match:
        for candidate in [preferred_name, *FONT_CANDIDATES]:
            try:
                result = run_cmd([fc_match, "-f", "%{file}", candidate], check=False)
                font_file = (result.stdout or "").strip()
                if font_file and Path(font_file).exists():
                    return font_file
            except Exception:
                continue
    # The bundled CJK font is a last resort only. Returning it first caused
    # Vietnamese subtitles to render as missing-glyph boxes.
    return str(BUNDLED_FONT) if BUNDLED_FONT.is_file() else None


def write_srt(segments: list[dict[str, Any]], output_path: Path) -> None:
    blocks = []
    srt_index = 0
    for index, segment in enumerate(segments):
        if segment.get("skip_subtitle"):
            continue
        text = wrap_text(segment["text"])
        if not text:
            continue
        render_start, render_end = subtitle_render_window(segments, index)
        srt_index += 1
        blocks.append(
            "\n".join(
                [
                    str(srt_index),
                    f"{format_time(render_start)} --> {format_time(render_end)}",
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
    style = normalize_subtitle_style({**DEFAULT_SUB_STYLE, **style})
    style_line = (
        "Style: Default,{font_name},{font_size},{primary_color},{primary_color},"
        "{outline_color},{back_color},0,0,0,0,100,100,0,0,{border_style},{outline},{shadow},"
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
    for index, segment in enumerate(segments):
        if segment.get("skip_subtitle"):
            continue
        render_start, render_end = subtitle_render_window(segments, index)
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
                    start=format_time(render_start, for_ass=True),
                    end=format_time(render_end, for_ass=True),
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
                start=format_time(render_start, for_ass=True),
                end=format_time(render_end, for_ass=True),
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


def transcribe_chinese_video(model_name: str, video_path: Path, job_id: str | None = None) -> list[dict[str, Any]]:
    progress = make_asr_progress_callback(job_id) if job_id else None
    return normalize_segments(ASR_SERVICE.transcribe_chinese(model_name, video_path, progress=progress))


def transcribe_video(
    model_name: str,
    video_path: Path,
    source_language: str,
    job_id: str | None = None,
    media_duration: float | None = None,
    *,
    progress: Callable[[float, float | None], None] | None = None,
) -> tuple[list[dict[str, Any]], str | None, float | None]:
    if progress is None and job_id:
        progress = make_asr_progress_callback(job_id, media_duration)
    result = ASR_SERVICE.transcribe(
        model_name, video_path, progress=progress, source_language=source_language
    )
    detected = result.detected_language
    if detected:
        try:
            detected = normalize_language(detected)
        except ValueError:
            detected = None
    return normalize_segments(result.segments), detected, result.language_probability


def format_asr_duration(seconds: float | None) -> str:
    value = max(0, int(seconds or 0))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def asr_progress_detail(position: float, total: float | None) -> str:
    current = format_asr_duration(position)
    return f"Đã nhận diện {current} / {format_asr_duration(total)}" if total else f"Đã nhận diện {current}"


def make_asr_progress_callback(
    job_id: str, expected_duration: float | None = None,
) -> Callable[[float, float | None], None]:
    """Report recognised audio time so a long transcription is visibly moving."""

    last_reported = -1

    def report(position: float, total: float | None) -> None:
        nonlocal last_reported
        ensure_job_active(job_id)
        effective_total = total or expected_duration
        if not effective_total:
            update_job(job_id, step_detail=asr_progress_detail(position, None))
            return
        percent = min(100, int(100 * position / effective_total))
        if percent - last_reported < 2 and percent != 100:
            return
        last_reported = percent
        update_job(
            job_id,
            progress=0.22 + 0.16 * percent / 100,
            step_detail=asr_progress_detail(position, effective_total),
        )

    return report


def update_job(job_id: str, **fields: Any) -> None:
    current = JOB_SERVICE.get(job_id)
    if current and current.get("status") == "cancelled" and fields.get("status") != "cancelled":
        return
    if current and current.get("cancel_requested"):
        if fields.get("status") == "processing":
            fields.pop("status")
        elif fields.get("status") == "completed":
            raise JobCancelled("Job đã được yêu cầu hủy trước khi hoàn tất")
        elif fields.get("status") == "failed" and current.get("status") == "cancelling":
            # The worker stopped because of an error after the user cancelled;
            # report the cancellation instead of leaving the job in cancelling.
            fields = {
                "status": "cancelled",
                "step": "cancelled",
                "step_detail": "Đã hủy theo yêu cầu",
                "finished_at": fields.get("finished_at") or utc_now(),
            }
    updated = JOB_SERVICE.update(job_id, **fields)
    if N8N_WORKFLOW_CONFIG.enabled:
        WORKFLOW_EVENTS.emit_transition(job_id, current, updated)
        notify_workflow_dispatcher()


def record_job_stage_measurement(job_id: str, measurement: StageMeasurement) -> None:
    """Persist additive, content-free stage metrics in the existing job payload."""

    # OCR and ASR may finish together; serialise the read-merge-write so neither metric is lost.
    with _stage_record_lock:
        current = JOB_SERVICE.get(job_id)
        if current is None:
            return
        stored = current.get("stage_metrics")
        stage_metrics = dict(stored) if isinstance(stored, dict) else {}
        stage_metrics[measurement.stage] = measurement.as_dict()
        JOB_SERVICE.update(job_id, stage_metrics=stage_metrics)


def measure_job_stage(job_id: str, stage: str, *, video_duration: float | None = None):
    return measure_stage(
        stage,
        video_duration=video_duration,
        on_complete=lambda measurement: record_job_stage_measurement(job_id, measurement),
    )


def record_job_stage_cache(job_id: str, stage: str, hit: bool) -> None:
    with _stage_record_lock:
        current = JOB_SERVICE.get(job_id)
        if current is None:
            return
        stored = current.get("stage_cache")
        stage_cache = dict(stored) if isinstance(stored, dict) else {}
        stage_cache[stage] = "hit" if hit else "miss"
        JOB_SERVICE.update(job_id, stage_cache=stage_cache)


def emit_workflow_event(event_type: str, job_id: str, job: dict[str, Any] | None = None) -> None:
    if not N8N_WORKFLOW_CONFIG.enabled:
        return
    current = job or JOB_SERVICE.get(job_id)
    if current is None:
        return
    WORKFLOW_EVENTS.emit(event_type, job_id, current)
    notify_workflow_dispatcher()


def ensure_job_active(job_id: str) -> None:
    if JOB_SERVICE.is_cancel_requested(job_id):
        raise JobCancelled("Job đã được người dùng yêu cầu hủy")


def make_ocr_progress_callback(job_id: str) -> Callable[[int, int], None]:
    """Persist at most about 100 OCR progress updates for one job."""

    last_reported = 0

    def report(completed: int, total: int) -> None:
        nonlocal last_reported
        ensure_job_active(job_id)
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


class SiblingStageFailed(RuntimeError):
    """Stops a parallel stage early because the stage running beside it failed."""


class SourceRecognitionProgress:
    """One monotonic progress bar for OCR and ASR while both run at once.

    Separate callbacks would overwrite each other's progress/detail and make the
    bar jump backwards. Throttling matches the sequential callbacks.
    """

    START = 0.12
    SPAN = 0.26

    def __init__(self, job_id: str, expected_duration: float | None = None) -> None:
        self.job_id = job_id
        self.expected_duration = expected_duration
        self.aborted = threading.Event()
        self._lock = threading.Lock()
        self._ocr = (0.0, "OCR")
        self._asr = (0.0, asr_progress_detail(0, expected_duration))
        self._last_ocr = 0
        self._last_asr_percent = -1
        self._reported = self.START

    def _check(self) -> None:
        if self.aborted.is_set():
            raise SiblingStageFailed("parallel recognition stage failed")
        ensure_job_active(self.job_id)

    def report_ocr(self, completed: int, total: int) -> None:
        self._check()
        if completed != total and completed - self._last_ocr < max(1, total // 100):
            return
        self._last_ocr = completed
        self._publish(ocr=(min(1.0, completed / max(total, 1)), f"OCR {completed}/{total} frames"))

    def report_asr(self, position: float, total: float | None) -> None:
        self._check()
        effective_total = total or self.expected_duration
        if not effective_total:
            self._publish(asr=(0.0, asr_progress_detail(position, None)))
            return
        percent = min(100, int(100 * position / effective_total))
        if percent - self._last_asr_percent < 2 and percent != 100:
            return
        self._last_asr_percent = percent
        self._publish(asr=(percent / 100, asr_progress_detail(position, effective_total)))

    def _publish(self, *, ocr: tuple[float, str] | None = None, asr: tuple[float, str] | None = None) -> None:
        with self._lock:
            if ocr is not None:
                self._ocr = ocr
            if asr is not None:
                self._asr = asr
            # ASR retries restart from 0; never move the bar backwards.
            self._reported = max(self._reported, self.START + self.SPAN * (self._ocr[0] + self._asr[0]) / 2)
            update_job(
                self.job_id,
                progress=self._reported,
                step_detail=f"{self._ocr[1]} · {self._asr[1]}",
            )


def run_job_stages_in_parallel(
    job_id: str, tracker: SourceRecognitionProgress, *stages: Callable[[], Any]
) -> list[Any]:
    """Run independent stages on threads; the first real error stops the others."""

    def run(stage: Callable[[], Any]) -> Any:
        # Worker threads must see the job so FFmpeg helpers still honour cancellation.
        _job_context.job_id = job_id
        try:
            return stage()
        except BaseException:
            tracker.aborted.set()
            raise
        finally:
            del _job_context.job_id

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(stages), thread_name_prefix=f"job-{job_id[:8]}"
    ) as executor:
        futures = {executor.submit(run, stage): index for index, stage in enumerate(stages)}
        try:
            results = collect_indexed_futures(futures)
        except SiblingStageFailed:
            # Only raised after another stage failed; surface that original error instead.
            for future in futures:
                exc = future.exception()
                if exc is not None and not isinstance(exc, SiblingStageFailed):
                    raise exc from None
            raise
    return [results[index] for index in range(len(stages))]


async def tts_edge_sync(
    text: str, output_path: Path, voice_name: str, rate: str, pitch: str = "+0Hz"
) -> None:
    if edge_tts is None:
        raise RuntimeError("edge-tts is not available")
    communicate = edge_tts.Communicate(text=text, voice=voice_name, rate=rate, pitch=pitch)
    await communicate.save(str(output_path))


EDGE_TTS_ATTEMPTS = 4
EDGE_TTS_RETRY_DELAY_SECONDS = 0.6


def tts_edge_with_retry(
    text: str, output_path: Path, voice_name: str, rate: str, pitch: str = "+0Hz"
) -> None:
    """Edge TTS drops requests intermittently; retry before giving up.

    Falling back to gTTS swaps in an unrelated voice with no male/female or
    pitch control, which is far more jarring than a short retry delay.
    """
    for attempt in range(1, EDGE_TTS_ATTEMPTS + 1):
        try:
            asyncio.run(tts_edge_sync(text, output_path, voice_name, rate, pitch))
            return
        except Exception as exc:
            if attempt == EDGE_TTS_ATTEMPTS:
                raise
            logger.info("edge-tts attempt %d failed (%s); retrying", attempt, type(exc).__name__)
            time.sleep(EDGE_TTS_RETRY_DELAY_SECONDS * attempt)


def tts_gtts_sync(text: str, output_path: Path, slow: bool = False, language: str = "vi") -> None:
    gTTS(text=text, lang=language_profile(language).gtts_code, slow=slow).save(str(output_path))


def is_speakable_tts_text(value: Any) -> bool:
    """Return true when text contains at least one letter or number.

    Some translation/OCR cues contain only punctuation.  They are non-empty
    strings, but gTTS removes every token and raises "No text to send to TTS
    API".  Unicode categories keep this check language-independent.
    """

    text = " ".join(str(value or "").split())
    return any(unicodedata.category(character)[0] in {"L", "N"} for character in text)


def synthesize_tts_segments(
    segments: list[dict[str, Any]],
    work_dir: Path,
    voice_type: str,
    speech_rate: float,
    voice_style: str = "natural",
    job_id: str | None = None,
    target_language: str = "vi",
    tts_provider: str = "edge",
    tts_voices: dict[str, str] | None = None,
    relax_pace: bool = False,
) -> list[Path]:
    tts_dir = work_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    preset = voice_preset(voice_style)
    tts_provider = normalize_tts_provider(tts_provider)
    rate_percent = int((speech_rate - 1.0) * 100) + preset.rate_percent
    base_rate_string = f"{rate_percent:+d}%"

    import concurrent.futures
    import threading
    completed_count = 0
    total_valid = sum(
        1
        for segment in segments
        if not segment.get("skip_tts") and is_speakable_tts_text(segment.get("text"))
    )
    progress_lock = threading.Lock()

    def run_tts_ffmpeg(args: list[str]) -> subprocess.CompletedProcess[str]:
        if job_id:
            ensure_job_active(job_id)
        result = run_ffmpeg(args, job_id=job_id)
        if job_id:
            ensure_job_active(job_id)
        return result

    def process_segment(index: int, segment: dict[str, Any]) -> Path | None:
        nonlocal completed_count
        if job_id:
            ensure_job_active(job_id)

        # Skip non-meaningful segments (filler words, hallucinations, etc.)
        if segment.get("skip_tts"):
            return None

        # Clean text
        raw_text = " ".join(str(segment.get("text") or "").split())
        if not is_speakable_tts_text(raw_text):
            segment["skip_tts"] = True
            segment["tts_skip_reason"] = "no_speakable_text"
            return None

        # Read the translation exactly as subtitled. Source-language word gaps must not
        # become commas here: word order differs between languages, so a pause mapped by
        # character position lands inside Vietnamese compounds ("vỡ, nát", "héo, mòn").
        text = raw_text

        output_path = tts_dir / f"{index:04d}.mp3"
        selected_voice_type = str(segment.get("voice_type", voice_type))
        profile = language_profile(target_language)
        voice_name = profile.edge_voice(selected_voice_type)
        # Auto voice routing nudges each speaker turn towards the source F0;
        # Edge applies it inside synthesis, which keeps formants natural.
        pitch_hz = preset.pitch_hz + int(segment.get("voice_pitch_offset_hz") or 0)
        pitch_hz = max(-MAX_TTS_PITCH_OFFSET_HZ, min(MAX_TTS_PITCH_OFFSET_HZ, pitch_hz))
        pitch_string = f"{pitch_hz:+d}Hz"

        # Pass 1: generate at the requested expressive rate and measure it.
        # When a line is too long, a second native TTS render is preferable to
        # a large FFmpeg atempo shift, which flattens Vietnamese prosody.
        edge_succeeded = False
        effective_rate_percent = rate_percent
        try:
            if tts_provider == "azure":
                synthesize_azure(text, output_path, voice_name=voice_name, rate_percent=rate_percent, pitch_hz=pitch_hz)
            elif tts_provider == "elevenlabs":
                synthesize_elevenlabs(
                    text,
                    output_path,
                    voice_type=selected_voice_type,
                    voice_style=voice_style,
                    speed=1.0 + (rate_percent / 100.0),
                    voice_key=(tts_voices or {}).get(selected_voice_type),
                )
            elif tts_provider == "cit":
                voice_name = (tts_voices or {}).get(selected_voice_type) or voice_name
                synthesize_cit(
                    text,
                    output_path,
                    voice_type=selected_voice_type,
                    voice_key=(tts_voices or {}).get(selected_voice_type),
                    speed=1.0 + (rate_percent / 100.0),
                    language=target_language,
                )
            else:
                tts_edge_with_retry(text, output_path, voice_name, base_rate_string, pitch_string)
                edge_succeeded = True
        except Exception as exc:
            if tts_provider != "edge":
                raise
            logger.warning("edge TTS failed, fallback to gTTS: %s", exc)
            tts_gtts_sync(text, output_path, slow=speech_rate < 0.95, language=target_language)

        cue_duration = available_voice_window(segments, index)

        # Trim only the outer silence. Reversing for the tail is important:
        # stop_periods=1 would stop at the first natural pause inside a line.
        trimmed_path = tts_dir / f"{index:04d}_trimmed.wav"
        trim_filter = (
            "silenceremove=start_periods=1:start_duration=0.08:start_threshold=-50dB,"
            "areverse,"
            "silenceremove=start_periods=1:start_duration=0.08:start_threshold=-50dB,"
            "areverse"
        )
        if job_id:
            ensure_job_active(job_id)
        run_tts_ffmpeg(
            [
                FFMPEG,
                "-y",
                "-i",
                str(output_path),
                "-af",
                trim_filter,
                "-ac",
                "1",
                "-ar",
                "24000",
                str(trimmed_path),
            ]
        )
        rendered_duration = get_media_duration(trimmed_path)

        native_rate_boost = adaptive_synthesis_rate_percent(rendered_duration, cue_duration)
        if not native_rate_boost and relax_pace:
            native_rate_boost = relaxed_synthesis_rate_percent(rendered_duration, cue_duration)
        applied_native_rate_boost = 0
        if edge_succeeded and native_rate_boost:
            adaptive_output_path = tts_dir / f"{index:04d}_adaptive.mp3"
            adaptive_trimmed_path = tts_dir / f"{index:04d}_adaptive.wav"
            candidate_rate_percent = rate_percent + native_rate_boost
            candidate_rate_string = f"{candidate_rate_percent:+d}%"
            try:
                tts_edge_with_retry(
                    text, adaptive_output_path, voice_name, candidate_rate_string, pitch_string
                )
                run_tts_ffmpeg(
                    [
                        FFMPEG,
                        "-y",
                        "-i",
                        str(adaptive_output_path),
                        "-af",
                        trim_filter,
                        "-ac",
                        "1",
                        "-ar",
                        "24000",
                        str(adaptive_trimmed_path),
                    ]
                )
                adaptive_trimmed_path.replace(trimmed_path)
                rendered_duration = get_media_duration(trimmed_path)
                effective_rate_percent = candidate_rate_percent
                applied_native_rate_boost = native_rate_boost
            except JobCancelled:
                raise
            except Exception as exc:
                logger.warning("Adaptive edge-tts render failed; keeping first pass: %s", type(exc).__name__)
            finally:
                adaptive_output_path.unlink(missing_ok=True)
                adaptive_trimmed_path.unlink(missing_ok=True)

        speed_factor = bounded_tempo(rendered_duration, cue_duration)
        fitted_path = trimmed_path
        if speed_factor > 1.0:
            fitted_path = tts_dir / f"{index:04d}_fitted.wav"
            run_tts_ffmpeg(
                [
                    FFMPEG,
                    "-y",
                    "-i",
                    str(trimmed_path),
                    "-af",
                    build_atempo_filter(speed_factor),
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    str(fitted_path),
                ]
            )
        segment["tts_speed_factor"] = round(speed_factor, 3)
        segment["tts_voice_style"] = preset.key
        segment["tts_voice_name"] = voice_name
        segment["tts_voice_rate"] = f"{effective_rate_percent:+d}%"
        segment["tts_native_rate_boost"] = applied_native_rate_boost
        segment["tts_voice_pitch"] = pitch_string
        segment["tts_duration"] = round(get_media_duration(fitted_path), 3)
        segment["tts_target_duration"] = round(cue_duration, 3)
        segment["tts_overrun"] = round(max(0.0, segment["tts_duration"] - cue_duration), 3)
        segment["tts_fit_quality"] = (
            "exact" if segment["tts_overrun"] <= 0.05
            else "soft" if segment["tts_overrun"] <= 0.35
            else "overflow"
        )
        segment["tts_path"] = fitted_path

        with progress_lock:
            completed_count += 1
            if job_id and (completed_count == total_valid or completed_count % max(1, total_valid // 15) == 0):
                update_job(
                    job_id,
                    progress=0.55 + (0.15 * completed_count / max(total_valid, 1)),
                    step_detail=f"Đã tạo giọng đọc {completed_count}/{total_valid} câu",
                )
        return fitted_path

    paths: list[Path] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(process_segment, index, segment): index
            for index, segment in enumerate(segments)
        }
        collect_indexed_futures(futures)

    for segment in segments:
        if "tts_path" in segment:
            paths.append(segment["tts_path"])

    return paths


MAX_TTS_PITCH_OFFSET_HZ = 40
VOICE_FADE_IN_SECONDS = 0.012
VOICE_FADE_OUT_SECONDS = 0.04


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


# One ffmpeg process opens every input at once; the container allows ~1024 file
# descriptors, so long videos (1000+ voice clips) failed with "Too many open files".
# Clips are summed in batches into float intermediates; summing is linear, so the
# result matches a single-pass mix.
VOICE_MIX_BATCH_SIZE = 128


def _mix_delayed_clips(
    clips: list[tuple[Path, int, str, str]], output: Path, work_dir: Path, *, final_chain: str, depth: int = 0
) -> None:
    """Mix (path, delay_ms, pre_filters, post_filters) clips with adelay + amix in bounded batches."""
    intermediates: list[Path] = []
    try:
        if len(clips) > VOICE_MIX_BATCH_SIZE:
            batched: list[tuple[Path, int, str, str]] = []
            for batch_index, offset in enumerate(range(0, len(clips), VOICE_MIX_BATCH_SIZE)):
                batch = clips[offset : offset + VOICE_MIX_BATCH_SIZE]
                batch_delay = min(delay for _, delay, _, _ in batch)
                partial = work_dir / f"voice_mix_{depth}_{batch_index:04d}.wav"
                intermediates.append(partial)
                _mix_delayed_clips(
                    [(path, delay - batch_delay, pre, post) for path, delay, pre, post in batch],
                    partial,
                    work_dir,
                    final_chain="",
                    depth=depth + 1,
                )
                batched.append((partial, batch_delay, "", ""))
            _mix_delayed_clips(batched, output, work_dir, final_chain=final_chain, depth=depth + 1)
            return
        input_args: list[str] = []
        filter_parts: list[str] = []
        labels: list[str] = []
        for index, (path, delay_ms, pre, post) in enumerate(clips):
            input_args.extend(["-i", str(path)])
            chain = ",".join(part for part in (pre, f"adelay={delay_ms}|{delay_ms}", post) if part)
            filter_parts.append(f"[{index}:a]{chain}[a{index}]")
            labels.append(f"[a{index}]")
        mix = f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0"
        filter_parts.append(f"{mix},{final_chain}[out]" if final_chain else f"{mix}[out]")
        # Intermediates stay float so partial sums above 0 dBFS are not clipped
        # before the final limiter.
        codec_args = [] if final_chain else ["-c:a", "pcm_f32le"]
        run_ffmpeg([FFMPEG, "-y", *input_args, "-filter_complex", ";".join(filter_parts), "-map", "[out]", *codec_args, str(output)])
    finally:
        for partial in intermediates:
            partial.unlink(missing_ok=True)


def build_voice_track(segments: list[dict[str, Any]], work_dir: Path, video_duration: float) -> Path:
    voice_track = work_dir / "voice_track.wav"
    clips: list[tuple[Path, int, str, str]] = []
    schedule_voice_segments(segments, video_duration=video_duration)
    for segment in segments:
        tts_path = segment.get("tts_path")
        if not tts_path:
            continue
        tts_start = float(segment.get("tts_start", segment["start"]))
        if tts_start >= video_duration:
            continue
        delay_ms = max(0, int(tts_start * 1000))
        gain = voice_clip_gain(Path(tts_path))
        segment["tts_level_gain"] = round(gain, 3)
        duration = float(segment.get("tts_duration") or 0.0)
        # Short fades remove clicks where the trimmed clip starts/ends mid-wave.
        fades = f"afade=t=in:d={VOICE_FADE_IN_SECONDS}"
        if duration > VOICE_FADE_OUT_SECONDS * 2:
            fades += f",afade=t=out:st={duration - VOICE_FADE_OUT_SECONDS:.3f}:d={VOICE_FADE_OUT_SECONDS}"
        clips.append((Path(tts_path), delay_ms, fades, f"volume={DEFAULT_VOICE_MIX_GAIN * gain:.3f}"))
    if not clips:
        run_ffmpeg(
            [
                FFMPEG,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-t",
                f"{video_duration:.3f}",
                str(voice_track),
            ]
        )
        return voice_track
    _mix_delayed_clips(
        clips,
        voice_track,
        work_dir,
        final_chain=(
            f"alimiter=limit=0.95,apad=whole_dur={video_duration:.3f},"
            f"atrim=duration={video_duration:.3f}"
        ),
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


def voice_clip_gain(clip_path: Path) -> float:
    """Gain that brings a TTS clip to the common speech level.

    Male and female Edge voices, and lines rendered at different rates, come
    out at noticeably different loudness; matching them avoids volume jumps.
    """
    try:
        samples, _ = read_pcm_wav(clip_path)
    except (OSError, ValueError, EOFError):
        return 1.0
    return speech_level_gain(samples)


def extract_voice_analysis_audio(video_path: Path, output_path: Path) -> Path:
    """Create the compact mono PCM input used by the local pitch router."""
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            # Drop mains hum/bass and hiss outside the voice F0 + low harmonics.
            "-af",
            "highpass=f=65,lowpass=f=1500",
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
    source_probe: MediaProbe | None = None,
    output_path: Path | None = None,
) -> Optional[Path]:
    if mode == 1:
        return None

    output_audio = output_path or (work_dir / "mixed_audio.wav")
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    input_args: list[str] = []
    filter_parts: list[str] = []
    labels: list[str] = []
    input_index = 0
    ducked = False

    if voice_track:
        input_args.extend(["-i", str(voice_track)])
        filter_parts.append(f"[{input_index}:a]volume=1.0,alimiter=limit=0.95,asplit=2[v][vkey]")
        labels.append("[v]")
        input_index += 1

    source_has_audio = source_probe.has_audio if source_probe is not None else has_audio_stream(video_path)
    if keep_original_audio and source_has_audio:
        original_audio = extract_original_audio(video_path, work_dir / "original_audio.wav")
        input_args.extend(["-i", str(original_audio)])
        if voice_track:
            filter_parts.append(
                f"[{input_index}:a]volume={original_audio_volume:.2f},{ORIGINAL_DUCK_EQ}[o_raw];"
                f"[o_raw][vkey]{ORIGINAL_DUCK_FILTER}[o]"
            )
            ducked = True
        else:
            filter_parts.append(f"[{input_index}:a]volume={original_audio_volume:.2f}[o]")
        labels.append("[o]")
        input_index += 1
    elif keep_original_audio:
        logger.info("Source video has no audio stream; continuing with generated audio only")

    if background_music:
        duration = source_probe.duration if source_probe is not None else get_media_duration(video_path)
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
    if voice_track and not ducked:
        filter_parts.append("[vkey]anullsink")

    target_duration = source_probe.duration if source_probe is not None else get_media_duration(video_path)
    filter_parts.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,"
        f"loudnorm=I=-16:LRA=11:TP=-1.5,apad=whole_dur={target_duration:.3f},"
        f"atrim=duration={target_duration:.3f}[out]"
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
    for index, segment in enumerate(segments):
        if segment.get("skip_subtitle"):
            continue
        render_start, render_end = subtitle_render_window(segments, index)
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
            f"fontcolor={subtitle_style.get('text_color_css', '#FFFFFF')}",
            "line_spacing=6",
            f"x={x_expression}",
            f"y={y_expression}",
            f"box={1 if subtitle_style.get('background_box', True) else 0}",
            f"boxcolor={subtitle_style.get('background_color_css', '#000000')}@{float(subtitle_style.get('background_opacity', 0.82)):.2f}",
            "boxborderw=12",
            f"enable='between(t,{render_start:.3f},{render_end:.3f})'",
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
    video_fps: float | None = None,
) -> None:
    Image, ImageDraw, ImageFont = _load_pillow()
    frames_dir = work_dir / "frames"
    rendered_dir = work_dir / "rendered_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    rendered_dir.mkdir(parents=True, exist_ok=True)

    fps = video_fps if video_fps is not None and video_fps > 0 else get_video_fps(video_path)
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
    text_rgb = tuple(
        int(subtitle_style.get("text_color_css", "#FFFFFF")[offset:offset + 2], 16)
        for offset in (1, 3, 5)
    )
    background_rgb = tuple(
        int(subtitle_style.get("background_color_css", "#000000")[offset:offset + 2], 16)
        for offset in (1, 3, 5)
    )
    background_alpha = round(float(subtitle_style.get("background_opacity", 0.82)) * 255)
    frame_paths = sorted(frames_dir.glob("frame_*.png"))

    render_windows = [subtitle_render_window(segments, index) for index in range(len(segments))]
    for frame_index, frame_path in enumerate(frame_paths):
        timestamp = frame_index / fps
        active = next(
            (
                segment
                for segment_index, segment in enumerate(segments)
                if not segment.get("skip_subtitle")
                and render_windows[segment_index][0] <= timestamp <= render_windows[segment_index][1]
            ),
            None,
        )
        image = Image.open(frame_path).convert("RGBA")
        if subtitle_style.get("background_style") == "blurred_bottom":
            from PIL import ImageFilter

            band_top = int(image.height * 0.68)
            blurred_band = image.crop((0, band_top, image.width, image.height)).filter(
                ImageFilter.GaussianBlur(radius=max(8, image.height // 45))
            )
            tint = Image.new("RGBA", blurred_band.size, (*background_rgb, 140))
            blurred_band = Image.alpha_composite(blurred_band, tint)
            image.paste(blurred_band, (0, band_top))
        output_frame = rendered_dir / frame_path.name
        if not active:
            image.convert("RGB").save(output_frame)
            continue
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
        if subtitle_style.get("background_box", True):
            draw.rounded_rectangle(
                (x - pad_x, y - pad_y, x + text_w + pad_x, y + text_h + pad_y),
                radius=18,
                fill=(*background_rgb, background_alpha),
            )
        draw.multiline_text((x, y), text, font=font, fill=(*text_rgb, 255), spacing=6, align="center")
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
    video_fps: float | None = None,
) -> None:
    subtitle_filter = f"subtitles=filename='{ff_escape_path(subtitle_path)}'"
    if BUNDLED_FONT.is_file():
        # Make the bundled CJK font available for libass fallback, but honor
        # the FontName written in the ASS file. Forcing CJK here breaks all
        # Vietnamese glyphs even when the chosen style supports them.
        subtitle_filter += f":fontsdir='{ff_escape_path(FONT_DIR)}'"
    if subtitle_style.get("background_style") == "blurred_bottom":
        tint = subtitle_style.get("background_color_css", "#000000")
        subtitle_filter = (
            "split[subtitle_base][subtitle_blur];"
            "[subtitle_blur]crop=iw:trunc(ih*0.32):0:trunc(ih*0.68),"
            f"boxblur=16:2,drawbox=color={tint}@0.55:t=fill[subtitle_band];"
            f"[subtitle_base][subtitle_band]overlay=0:H-h,{subtitle_filter}"
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
        if subtitle_style.get("background_style") == "blurred_bottom":
            tint = subtitle_style.get("background_color_css", "#000000")
            fallback_filter = (
                "split[subtitle_base][subtitle_blur];"
                "[subtitle_blur]crop=iw:trunc(ih*0.32):0:trunc(ih*0.68),"
                f"boxblur=16:2,drawbox=color={tint}@0.55:t=fill[subtitle_band];"
                f"[subtitle_base][subtitle_band]overlay=0:H-h,{fallback_filter}"
            )
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
            burn_subtitles_with_pillow(
                video_path,
                output_path,
                audio_path,
                segments,
                subtitle_style,
                work_dir,
                video_fps=video_fps,
            )


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


def recognize_video_source(
    *,
    job_id: str,
    video_path: Path,
    work_dir: Path,
    source_probe: MediaProbe,
    subtitle_source: str,
    source_language: str,
    whisper_model: str,
    ocr_sample_fps: float,
    ocr_roi_top: float,
    ocr_roi_bottom: float,
    segmentation_options: SubtitleSegmentationOptions | None = None,
) -> tuple[list[dict[str, Any]], str | None, float | None, str, int]:
    """Run OCR/ASR and return filtered, checkpoint-ready source segments."""

    video_duration = source_probe.duration
    ocr_segments: list[dict[str, Any]] = []
    ocr_requested = subtitle_source in {"auto", "independent", "burned"} and source_language == "zh-CN"
    run_ocr = False
    if ocr_requested and SETTINGS.ocr_backend == "off":
        message = "OCR is disabled on this runtime; using Whisper speech recognition"
        if subtitle_source in {"burned", "independent"}:
            raise RuntimeError(
                "OCR is disabled on this runtime. Set VIDTRANS_OCR_BACKEND=tesseract or paddle "
                "after installing the selected OCR runtime."
            )
        logger.warning(message)
        update_job(job_id, status="processing", step="transcribing", step_detail=message, progress=0.22)
    elif ocr_requested:
        run_ocr = True

    def recognize_burned_subtitles(progress: Callable[[int, int], None]) -> list[dict[str, Any]]:
        try:
            with measure_job_stage(job_id, "ocr", video_duration=video_duration):
                with _ocr_slots:
                    ensure_job_active(job_id)
                    return extract_burned_subtitle_segments(
                        video_path,
                        work_dir,
                        ffmpeg=FFMPEG,
                        config=OCRConfig(
                            sample_fps=ocr_sample_fps,
                            roi_top=ocr_roi_top,
                            roi_bottom=ocr_roi_bottom,
                        ),
                        reader_backend=SETTINGS.ocr_backend,
                        reader_model=SETTINGS.ocr_model,
                        progress_callback=progress,
                    )
        except (JobCancelled, SiblingStageFailed):
            raise
        except Exception:
            if subtitle_source in {"burned", "independent"}:
                raise
            logger.warning("OCR subtitle extraction failed; using speech transcription", exc_info=True)
            return []

    def recognize_speech(
        progress: Callable[[float, float | None], None] | None,
    ) -> tuple[list[dict[str, Any]], str | None, float | None]:
        with _whisper_slots:
            ensure_job_active(job_id)
            asr_language = "auto" if subtitle_source == "independent" else source_language
            return transcribe_video(
                whisper_model, video_path, asr_language, job_id, video_duration, progress=progress
            )

    def measured_speech(
        progress: Callable[[float, float | None], None],
    ) -> tuple[list[dict[str, Any]], str | None, float | None]:
        with measure_job_stage(job_id, "asr", video_duration=video_duration):
            return recognize_speech(progress)

    asr_segments: list[dict[str, Any]] = []
    detected_language: str | None = None
    detected_language_probability: float | None = None
    if run_ocr and source_probe.has_audio:
        # Frames and audio are independent inputs and both engines release the GIL,
        # so the stage takes max(OCR, ASR) instead of their sum. Merge rules below
        # still see exactly the same two segment lists.
        update_job(job_id, status="processing", step="extracting-subtitles", progress=0.12)
        tracker = SourceRecognitionProgress(job_id, video_duration)
        ocr_segments, (asr_segments, detected_language, detected_language_probability) = run_job_stages_in_parallel(
            job_id,
            tracker,
            lambda: recognize_burned_subtitles(tracker.report_ocr),
            lambda: measured_speech(tracker.report_asr),
        )
    else:
        if run_ocr:
            update_job(job_id, status="processing", step="extracting-subtitles", progress=0.12)
            ocr_segments = recognize_burned_subtitles(make_ocr_progress_callback(job_id))
        ensure_job_active(job_id)
        update_job(
            job_id,
            status="processing",
            step="transcribing",
            step_detail=asr_progress_detail(0, video_duration),
            progress=0.22,
        )
        with measure_job_stage(job_id, "asr", video_duration=video_duration):
            if source_probe.has_audio:
                asr_segments, detected_language, detected_language_probability = recognize_speech(None)
            elif not ocr_segments:
                raise RuntimeError(
                    "Video có hình ảnh nhưng không có âm thanh để nhận diện giọng nói. "
                    "Nếu video có phụ đề sẵn, hãy chọn OCR và bật OCR trên máy chủ; "
                    "nếu không, hãy chọn video có âm thanh."
                )
    if ocr_segments and subtitle_source == "independent":
        segments = merge_independent_ocr_asr_segments(
            ocr_segments,
            asr_segments,
            ocr_language=source_language,
            asr_language=detected_language,
        )
        source_method = "independent"
    elif ocr_segments and subtitle_source == "auto":
        segments = merge_ocr_and_asr_segments(ocr_segments, asr_segments)
        source_method = "hybrid"
    elif ocr_segments:
        segments = annotate_ocr_segments_with_asr(ocr_segments, asr_segments)
        source_method = "ocr"
    else:
        segments = asr_segments
        source_method = "speech"
        if segments and source_probe.width and source_probe.height:
            ensure_job_active(job_id)
            try:
                with measure_job_stage(job_id, "caption_alignment", video_duration=video_duration):
                    segments = align_speech_to_burned_captions(
                        video_path,
                        segments,
                        ffmpeg=FFMPEG,
                        frame_size=(source_probe.width, source_probe.height),
                        config=CaptionAlignmentConfig(roi_top=ocr_roi_top, roi_bottom=ocr_roi_bottom),
                    )
            except JobCancelled:
                raise
            except Exception:
                logger.warning("Caption alignment failed; keeping Whisper timing", exc_info=True)
    if not segments:
        raise RuntimeError(
            "Không nhận diện được phụ đề hoặc lời thoại nguồn. "
            "Kiểm tra vùng OCR, ngôn ngữ nguồn hoặc thử Whisper model lớn hơn."
        )

    segments = segment_subtitle_cues(segments, options=segmentation_options)
    segments = filter_meaningful_segments(segments)
    meaningful_count = sum(1 for segment in segments if not segment.get("skip_subtitle"))
    skipped_count = len(segments) - meaningful_count
    if skipped_count:
        logger.info(
            "Filtered %d non-meaningful segment(s); %d remain for translation",
            skipped_count,
            meaningful_count,
        )
    return segments, detected_language, detected_language_probability, source_method, skipped_count


def process_video(
    *,
    job_id: str,
    video_path: Path,
    mode: int,
    whisper_model: str,
    voice_type: str,
    voice_mode: str,
    voice_style: str,
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
    source_language: str = "zh-CN",
    target_language: str = "vi",
    generate_tiktok_post: bool = True,
    tiktok_max_summary_chars: int = 350,
    tiktok_hashtag_count: int = 6,
    auto_publish_tiktok: bool = False,
    tiktok_privacy_level: str = "SELF_ONLY",
    tiktok_publish_at: str | None = None,
    tts_provider: str = "edge",
    translation_provider: str = "google",
    tts_voices: dict[str, str] | None = None,
) -> None:
    _job_context.job_id = job_id
    current_job = JOB_SERVICE.get(job_id) or {}
    owner_user_id = str(current_job.get("owner_user_id") or _bootstrap_owner["user_id"])
    work_dir = tenant_job_dir(WORK_DIR, owner_user_id, job_id)
    output_dir = tenant_job_dir(OUTPUT_DIR, owner_user_id, job_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    subtitle_style = normalize_subtitle_style({**DEFAULT_SUB_STYLE, **(subtitle_style or {})})
    subtitle_style["font_name"] = language_profile(target_language).font_family
    try:
        JOB_SERVICE.update(job_id, stage_metrics={}, stage_cache={})
        ensure_job_active(job_id)
        with measure_job_stage(job_id, "preflight"):
            source_probe = probe_media_file(video_path, ffprobe=FFPROBE)
            source_checksum = sha256_file(
                video_path,
                check_active=lambda: ensure_job_active(job_id),
            )
        video_duration = source_probe.duration
        TENANT_GOVERNANCE.adjust(
            job_id=job_id,
            limit_minutes=job_plan_limit(job_id),
            minutes=TENANT_GOVERNANCE.minutes_for_duration(video_duration),
        )
        video_width, video_height = source_probe.width, source_probe.height
        checkpoint_store = StageCheckpointStore(work_dir)
        transcript_fingerprint = fingerprint_payload(
            {
                "version": 6,
                "source_checksum": source_checksum,
                "subtitle_source": subtitle_source,
                "source_language": source_language,
                "whisper_model": whisper_model,
                "asr_engine": ASR_SERVICE.engine_name,
                "asr_compute_type": ASR_SERVICE.config.compute_type,
                "asr_beam_size": ASR_SERVICE.config.beam_size,
                "asr_vad": ASR_SERVICE.config.vad,
                "asr_vad_min_silence_ms": ASR_SERVICE.config.vad_min_silence_ms,
                "asr_vad_gap_recovery": 2,
                "ocr_enabled": SETTINGS.ocr_backend != "off",
                "ocr_backend": SETTINGS.ocr_backend,
                "ocr_sample_fps": ocr_sample_fps,
                "ocr_roi_top": ocr_roi_top,
                "ocr_roi_bottom": ocr_roi_bottom,
                "ocr_asr_merge": 2,
                "segmentation": {
                    "preferred_chars": int(subtitle_style["preferred_cue_chars"]),
                    "max_chars": int(subtitle_style["max_cue_chars"]),
                    "pause_seconds": float(subtitle_style["cue_pause_seconds"]),
                    "max_duration_seconds": float(subtitle_style["max_cue_duration_seconds"]),
                },
                "filter": segment_filter_signature(),
            }
        )

        def compute_transcript_checkpoint() -> dict[str, Any]:
            (
                computed_segments,
                computed_language,
                computed_probability,
                computed_source_method,
                _computed_skipped_count,
            ) = recognize_video_source(
                job_id=job_id,
                video_path=video_path,
                work_dir=work_dir,
                source_probe=source_probe,
                subtitle_source=subtitle_source,
                source_language=source_language,
                whisper_model=whisper_model,
                ocr_sample_fps=ocr_sample_fps,
                ocr_roi_top=ocr_roi_top,
                ocr_roi_bottom=ocr_roi_bottom,
                segmentation_options=SubtitleSegmentationOptions(
                    preferred_chars=int(subtitle_style["preferred_cue_chars"]),
                    max_chars=int(subtitle_style["max_cue_chars"]),
                    pause_seconds=float(subtitle_style["cue_pause_seconds"]),
                    max_duration_seconds=float(subtitle_style["max_cue_duration_seconds"]),
                ),
            )
            return {
                "version": 1,
                "source_method": computed_source_method,
                "detected_language": computed_language,
                "detected_language_probability": computed_probability,
                "segments": computed_segments,
            }

        transcript_checkpoint, transcript_cache_hit = load_or_compute_json(
            checkpoint_store,
            "transcript",
            fingerprint=transcript_fingerprint,
            artifact_name=f"checkpoints/transcript/{transcript_fingerprint}.json",
            validator=valid_transcript_checkpoint,
            compute=compute_transcript_checkpoint,
        )
        transcript_payload = transcript_checkpoint.payload
        # Applied after the checkpoint so cached transcripts benefit without re-running ASR.
        segments = merge_fragment_cues(
            transcript_payload["segments"], max_gap_seconds=float(subtitle_style["cue_pause_seconds"])
        )
        detected_language = transcript_payload.get("detected_language")
        detected_language_probability = transcript_payload.get("detected_language_probability")
        source_method = str(transcript_payload["source_method"])
        skipped_count = sum(1 for segment in segments if segment.get("skip_subtitle"))
        transcript_path = work_dir / "transcript.json"
        write_json_atomic(transcript_path, segments)
        record_job_stage_cache(job_id, "transcript", transcript_cache_hit)
        if transcript_cache_hit:
            update_job(job_id, step_detail="Đã dùng lại kết quả nhận diện hợp lệ", progress=0.38)
        meaningful_count = sum(1 for segment in segments if not segment.get("skip_subtitle"))

        update_job(
            job_id,
            step="translating",
            progress=0.4,
            subtitle_source_used=source_method,
            review_cues=sum(1 for segment in segments if segment.get("needs_review")),
            skipped_cues=skipped_count,
        )
        ensure_job_active(job_id)
        effective_source_language = (
            detected_language if source_language == "auto" and detected_language else source_language
        )
        if effective_source_language == "auto":
            raise RuntimeError("Không xác định được ngôn ngữ nguồn từ âm thanh")
        if effective_source_language == target_language:
            raise RuntimeError("Ngôn ngữ nhận diện trùng với ngôn ngữ đích")
        translation_fingerprint = fingerprint_payload(
            {
                "version": 1,
                "transcript_checksum": transcript_checkpoint.checksum,
                "source_language": effective_source_language,
                "target_language": target_language,
                "translation_provider": translation_provider,
                "translation_pipeline_version": 5,
                "max_batch_chars": 1200,
            }
        )
        translation_checkpoint = checkpoint_store.load_json(
            "translation",
            fingerprint=translation_fingerprint,
            validator=valid_translation_checkpoint,
        )
        translation_cache_hit = translation_checkpoint is not None
        if translation_checkpoint is not None:
            translated_segments = list(translation_checkpoint.payload["segments"])
        else:
            with measure_job_stage(job_id, "translation", video_duration=video_duration):
                translation_kwargs = {
                    "target_language": target_language,
                    "check_active": lambda: ensure_job_active(job_id),
                    "progress_callback": lambda completed, total: update_job(
                        job_id,
                        progress=0.4 + 0.06 * completed / max(1, total),
                        step_detail=f"Đã kiểm tra bản dịch {completed}/{total} câu",
                    ),
                }
                if subtitle_source == "independent":
                    translated_segments = translate_segments_by_source_language(
                        segments,
                        default_source_language=effective_source_language,
                        translator_factory=lambda source, target: create_translation_provider(
                            translation_provider, source_language=source, target_language=target
                        ),
                        **translation_kwargs,
                    )
                else:
                    translated_segments = translate_segments(
                        segments,
                        create_translation_provider(
                            translation_provider,
                            source_language=effective_source_language,
                            target_language=target_language,
                        ),
                        source_language=effective_source_language,
                        **translation_kwargs,
                    )
        record_job_stage_cache(job_id, "translation", translation_cache_hit)
        if translation_cache_hit:
            update_job(job_id, step_detail="Đã dùng lại bản dịch hợp lệ", progress=0.46)
        translation_fallback_cues = sum(
            1 for segment in translated_segments if segment.get("translation_status") == "source_fallback"
        )
        translation_backup_cues = sum(1 for segment in translated_segments if segment.get("translation_fallback"))
        if translation_fallback_cues:
            translation_detail = f"Có {translation_fallback_cues} câu cần kiểm tra lại bản dịch"
        elif translation_backup_cues:
            translation_detail = (
                f"Đã dịch đầy đủ; {translation_backup_cues} câu phải dịch bằng nhà cung cấp dự phòng "
                "vì AI dịch đã chọn bị lỗi"
            )
        else:
            translation_detail = "Đã dịch đầy đủ các câu"
        update_job(
            job_id,
            translation_fallback_cues=translation_fallback_cues,
            translation_backup_cues=translation_backup_cues,
            review_cues=sum(1 for segment in translated_segments if segment.get("needs_review")),
            step_detail=translation_detail,
        )
        # Publish diagnostics even on failure, before any TTS, rendering or posting.
        translation_path = output_dir / f"{job_id}.translation.json"
        write_json_atomic(translation_path, {
            "version": 4,
            "source_method": source_method,
            "source_language": effective_source_language,
            "source_language_requested": source_language,
            "detected_language": detected_language,
            "detected_language_probability": detected_language_probability,
            "target_language": target_language,
            "translation_complete": translation_fallback_cues == 0 and meaningful_count > 0,
            "review_cues": sum(1 for s in translated_segments if s.get("needs_review")),
            "segments": translated_segments,
        })
        update_job(job_id, translation_file=stored_artifact_name(translation_path))
        ensure_translation_complete(translated_segments, target_language=target_language)
        if not translation_cache_hit:
            translation_checkpoint = checkpoint_store.commit_json(
                "translation",
                fingerprint=translation_fingerprint,
                artifact_name=f"checkpoints/translation/{translation_fingerprint}.json",
                payload={"version": 1, "segments": translated_segments},
            )
        update_job(job_id, step="translation-ready", progress=0.46)
        translated_segments = apply_subtitle_layout(
            translated_segments,
            video_width=video_width,
            video_height=video_height,
            style=subtitle_style,
        )

        srt_path = output_dir / f"{job_id}.srt"
        translation_path = output_dir / f"{job_id}.translation.json"
        ass_path = work_dir / f"{job_id}.ass"
        tiktok_json_path: Path | None = None
        tiktok_text_path: Path | None = None
        tiktok_post = None
        if generate_tiktok_post:
            ensure_job_active(job_id)
            update_job(job_id, step="summarizing", progress=0.47, step_detail=None)
            try:
                with measure_job_stage(job_id, "summary", video_duration=video_duration):
                    tiktok_post = TIKTOK_SUMMARY_PROVIDER.generate(
                        translated_segments,
                        max_summary_chars=tiktok_max_summary_chars,
                        hashtag_count=tiktok_hashtag_count,
                    )
                    tiktok_json_path, tiktok_text_path = write_tiktok_artifacts(
                        tiktok_post,
                        output_dir,
                        job_id,
                    )
            except Exception as exc:
                logger.warning("TikTok summary generation failed for job %s", job_id, exc_info=True)
                update_job(job_id, tiktok_error=str(exc))

        voice_summary: dict[str, int] | None = None
        if mode in (2, 3):
            ensure_job_active(job_id)
            with measure_job_stage(job_id, "voice_routing", video_duration=video_duration):
                if voice_mode == "auto":
                    update_job(job_id, step="routing-voices", progress=0.5)
                    try:
                        analysis_audio = extract_voice_analysis_audio(
                            video_path,
                            work_dir / "voice_analysis.wav",
                        )
                        content_voice, content_cues = infer_speaker_voice(translated_segments, target_language)
                        if content_voice:
                            logger.info(
                                "Dialogue implies a %s narrator (%d cues) for job %s",
                                content_voice,
                                content_cues,
                                job_id,
                            )
                        voice_summary = route_segments_by_pitch(
                            translated_segments,
                            analysis_audio,
                            fallback_voice=voice_type,
                            content_voice=content_voice,
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

        style_decision = None
        requested_voice_style = voice_style
        if mode in (2, 3) and voice_style == AUTO_VOICE_STYLE:
            style_decision = infer_voice_style(translated_segments)
            voice_style = style_decision.style
            update_job(job_id, step_detail=f"Chất giọng tự động: {voice_preset(voice_style).label_vi}")

        audio_path = None
        tts_checkpoint = None
        audio_checkpoint = None
        if mode in (2, 3):
            ensure_job_active(job_id)
            update_job(job_id, step="tts", progress=0.55)
            tts_fingerprint_fields = lambda provider: fingerprint_payload(  # noqa: E731
                {
                    "version": 1,
                    "translation_bundle_checksum": translation_checkpoint.bundle_checksum,
                    "source_checksum": source_checksum,
                    "target_language": target_language,
                    "voice_type": voice_type,
                    "voice_mode": voice_mode,
                    "voice_style": voice_style,
                    "requested_voice_style": requested_voice_style,
                    "tts_provider": provider,
                    "tts_voices": (tts_voices or {}) if provider == tts_provider else {},
                    "speech_rate": speech_rate,
                    "video_duration": round(video_duration, 3),
                    "routing": [
                        {
                            "voice_type": segment.get("voice_type"),
                            "voice_routing": segment.get("voice_routing"),
                        }
                        for segment in translated_segments
                    ],
                    "tts_pipeline_version": 4,
                }
            )
            tts_fingerprint = tts_fingerprint_fields(tts_provider)
            tts_checkpoint = checkpoint_store.load_json(
                "tts",
                fingerprint=tts_fingerprint,
                validator=lambda payload: valid_tts_checkpoint(payload)
                and payload.get("segment_count") == len(translated_segments),
            )
            tts_cache_hit = tts_checkpoint is not None
            if tts_checkpoint is not None:
                voice_track = restore_tts_checkpoint(
                    translated_segments,
                    tts_checkpoint.payload,
                    work_dir=work_dir,
                )
                update_job(job_id, step_detail="Đã dùng lại giọng đọc hợp lệ", progress=0.75)
            else:
                tts_work_dir = work_dir / "checkpoints" / "tts" / tts_fingerprint
                with measure_job_stage(job_id, "tts", video_duration=video_duration):
                    try:
                        synthesize_tts_segments(
                            translated_segments,
                            tts_work_dir,
                            voice_type,
                            speech_rate,
                            voice_style,
                            job_id=job_id,
                            target_language=target_language,
                            tts_provider=tts_provider,
                            tts_voices=tts_voices,
                            relax_pace=requested_voice_style == AUTO_VOICE_STYLE,
                        )
                    except Exception as exc:
                        blocked = tts_account_blocked(exc) if tts_provider != "edge" else None
                        if blocked is None:
                            raise
                        # The whole job switches, so the dub keeps one consistent voice. Edge audio is
                        # checkpointed under its own fingerprint: a retry after topping up uses the
                        # paid provider again.
                        provider_label = {"elevenlabs": "ElevenLabs", "azure": "Azure Speech", "cit": "CIT Voice Studio"}.get(tts_provider, tts_provider)
                        logger.warning("%s unusable for job %s (%s); dubbing with Edge TTS", provider_label, job_id, blocked)
                        update_job(job_id, step_detail=f"{provider_label} {blocked}; chuyển sang Edge TTS miễn phí",
                                   tts_fallback={"from": provider_label, "to": "Edge TTS", "reason": blocked})
                        # Fingerprint first: the lambda compares against the originally requested provider.
                        tts_fingerprint = tts_fingerprint_fields("edge")
                        tts_provider = "edge"
                        tts_work_dir = work_dir / "checkpoints" / "tts" / tts_fingerprint
                        synthesize_tts_segments(
                            translated_segments,
                            tts_work_dir,
                            voice_type,
                            speech_rate,
                            voice_style,
                            job_id=job_id,
                            target_language=target_language,
                            tts_provider="edge",
                            tts_voices={},
                            relax_pace=requested_voice_style == AUTO_VOICE_STYLE,
                        )
                    ensure_job_active(job_id)
                    voice_track = build_voice_track(
                        translated_segments, tts_work_dir, video_duration
                    )
                tts_payload, tts_dependencies = serialize_tts_checkpoint(
                    translated_segments,
                    work_dir=work_dir,
                    voice_track=voice_track,
                )
                tts_checkpoint = checkpoint_store.commit_json(
                    "tts",
                    fingerprint=tts_fingerprint,
                    artifact_name=f"checkpoints/tts/{tts_fingerprint}.json",
                    payload=tts_payload,
                    dependency_names=tts_dependencies,
                )
            record_job_stage_cache(job_id, "tts", tts_cache_hit)

            update_job(job_id, step="mixing-audio", progress=0.75)
            background_music_checksum = None
            if mode == 3 and background_music_path is not None:
                background_music_checksum = sha256_file(
                    background_music_path,
                    check_active=lambda: ensure_job_active(job_id),
                )
            audio_fingerprint = fingerprint_payload(
                {
                    "version": 1,
                    "tts_bundle_checksum": tts_checkpoint.bundle_checksum,
                    "source_checksum": source_checksum,
                    "mode": mode,
                    "background_music_checksum": background_music_checksum,
                    "keep_original_audio": keep_original_audio,
                    "original_audio_volume": original_audio_volume,
                    "music_volume": music_volume,
                    "audio_mix_version": 3,
                }
            )
            audio_checkpoint = checkpoint_store.load_json(
                "audio_mix",
                fingerprint=audio_fingerprint,
                validator=valid_file_checkpoint,
            )
            audio_cache_hit = audio_checkpoint is not None
            if audio_checkpoint is not None:
                audio_path = restore_file_checkpoint(audio_checkpoint.payload, work_dir=work_dir)
                update_job(job_id, step_detail="Đã dùng lại bản phối âm thanh hợp lệ", progress=0.85)
            else:
                with measure_job_stage(job_id, "audio_mix", video_duration=video_duration):
                    audio_path = mix_audio(
                        mode=mode,
                        video_path=video_path,
                        work_dir=work_dir,
                        voice_track=voice_track,
                        background_music=background_music_path if mode == 3 else None,
                        keep_original_audio=keep_original_audio,
                        original_audio_volume=original_audio_volume,
                        music_volume=music_volume,
                        source_probe=source_probe,
                        output_path=work_dir
                        / "checkpoints"
                        / "audio_mix"
                        / f"{audio_fingerprint}.wav",
                    )
                if audio_path is None:
                    raise RuntimeError("Audio mixing did not produce an artifact")
                audio_relative_path = str(audio_path.resolve().relative_to(work_dir.resolve()))
                audio_checkpoint = checkpoint_store.commit_json(
                    "audio_mix",
                    fingerprint=audio_fingerprint,
                    artifact_name=f"checkpoints/audio_mix/{audio_fingerprint}.json",
                    payload={"version": 1, "artifact": audio_relative_path},
                    dependency_names=[audio_relative_path],
                )
            record_job_stage_cache(job_id, "audio_mix", audio_cache_hit)

        # Write subtitle artifacts after TTS planning so diagnostics include
        # TTS fields, while visual timing remains anchored to source speech.
        with measure_job_stage(job_id, "subtitle_artifacts", video_duration=video_duration):
            write_srt(translated_segments, srt_path)
            write_ass(
                translated_segments,
                ass_path,
                subtitle_style,
                video_width=video_width,
                video_height=video_height,
            )
            serializable_segments = [
                {key: value for key, value in segment.items() if key != "tts_path"}
                for segment in translated_segments
            ]
            write_json_atomic(
                translation_path,
                {
                    "version": 4,
                    "source_language": effective_source_language,
                    "source_language_requested": source_language,
                    "detected_language": detected_language,
                    "target_language": target_language,
                    "translation_complete": True,
                    "source_method": source_method,
                    "review_cues": sum(1 for segment in translated_segments if segment.get("needs_review")),
                    "voice_routing": {
                        "mode": voice_mode,
                        "fallback_voice": voice_type,
                        "voice_style": voice_style,
                        "requested_voice_style": requested_voice_style,
                        "style_decision": style_decision.as_dict() if style_decision else None,
                        "summary": voice_summary,
                    },
                    "segments": serializable_segments,
                },
            )

        output_video = output_dir / f"output_{job_id}.mp4"
        render_fingerprint = fingerprint_payload(
            {
                "version": 1,
                "source_checksum": source_checksum,
                "translation_bundle_checksum": translation_checkpoint.bundle_checksum,
                "tts_bundle_checksum": tts_checkpoint.bundle_checksum if tts_checkpoint else None,
                "audio_bundle_checksum": audio_checkpoint.bundle_checksum
                if audio_checkpoint
                else None,
                "ass_checksum": sha256_file(ass_path),
                "subtitle_style": subtitle_style,
                "video_fps": source_probe.fps,
                "mode": mode,
                "render_pipeline_version": 2,
            }
        )
        render_artifact = (
            work_dir / "checkpoints" / "render" / f"{render_fingerprint}.mp4"
        )
        render_artifact.parent.mkdir(parents=True, exist_ok=True)
        ensure_job_active(job_id)
        update_job(job_id, step="rendering", progress=0.9)
        render_checkpoint = checkpoint_store.load_json(
            "render",
            fingerprint=render_fingerprint,
            validator=valid_file_checkpoint,
        )
        render_cache_hit = render_checkpoint is not None
        if render_checkpoint is not None:
            render_artifact = restore_file_checkpoint(render_checkpoint.payload, work_dir=work_dir)
            update_job(job_id, step_detail="Đã dùng lại video render hợp lệ", progress=0.98)
        else:
            with measure_job_stage(job_id, "render", video_duration=video_duration):
                burn_subtitles(
                    video_path,
                    ass_path,
                    render_artifact,
                    audio_path,
                    translated_segments,
                    subtitle_style,
                    work_dir,
                    video_fps=source_probe.fps,
                )
            render_relative_path = str(render_artifact.resolve().relative_to(work_dir.resolve()))
            render_checkpoint = checkpoint_store.commit_json(
                "render",
                fingerprint=render_fingerprint,
                artifact_name=f"checkpoints/render/{render_fingerprint}.json",
                payload={"version": 1, "artifact": render_relative_path},
                dependency_names=[render_relative_path],
            )
        record_job_stage_cache(job_id, "render", render_cache_hit)
        copy_file_atomic(render_artifact, output_video)
        ensure_job_active(job_id)

        artifact_fields: dict[str, Any] = {
            "output_video": stored_artifact_name(output_video),
            "subtitle_file": stored_artifact_name(srt_path),
            "translation_file": stored_artifact_name(translation_path),
            "transcript_file": str(transcript_path.relative_to(BASE_DIR)),
        }
        if tiktok_json_path and tiktok_text_path:
            artifact_fields.update(
                {
                    "tiktok_json_file": stored_artifact_name(tiktok_json_path),
                    "tiktok_text_file": stored_artifact_name(tiktok_text_path),
                }
            )

        tiktok_description = tiktok_post_description(tiktok_post)
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
                    tiktok_publish_title=tiktok_description,
                    tiktok_publish_privacy_level=tiktok_privacy_level,
                    job_action="publish_tiktok",
                    publish_request={
                        "video_path": str(output_video),
                        "title": tiktok_description,
                        "privacy_level": tiktok_privacy_level,
                    },
                    **artifact_fields,
                )
                return

        tiktok_publish_fields: dict[str, Any] = {}
        if auto_publish_tiktok:
            ensure_job_active(job_id)
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
                        "Không có caption đã tạo nên chưa thể tự động đăng TikTok"
                    )
                publish_result = tenant_tiktok_publisher(owner_user_id).publish(
                    output_video,
                    tiktok_description,
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

        if tiktok_publish_fields.get("tiktok_publish_id") and JOB_SERVICE.is_cancel_requested(job_id):
            finish_cancelled_after_publish(job_id, **artifact_fields, **tiktok_publish_fields)
            return

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
        finished_job = JOB_SERVICE.get(job_id) or {}
        tts_fallback = finished_job.get("tts_fallback")
        backup_cues = int(finished_job.get("translation_backup_cues") or 0)
        if backup_cues:
            completion_fields["step_detail"] = (
                f"Hoàn tất; {backup_cues} câu được dịch bằng nhà cung cấp dự phòng vì AI dịch đã chọn bị lỗi. "
                "Bấm Chạy lại nếu muốn dịch lại toàn bộ bằng AI đã chọn"
            )
        if tts_fallback:
            completion_fields["step_detail"] = (
                f"Đã lồng tiếng bằng Edge TTS vì {tts_fallback['from']} {tts_fallback['reason']}; "
                "nạp thêm credit rồi bấm Chạy lại nếu muốn dùng giọng đã chọn"
            )
        update_job(job_id, **completion_fields)
        TENANT_GOVERNANCE.consume(job_id)
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
        TENANT_GOVERNANCE.release(job_id)
    except Exception as exc:
        logger.exception("Video processing failed for job %s", job_id)
        update_job(
            job_id,
            status="failed",
            step="failed",
            error=public_error_message(exc),
            finished_at=utc_now(),
        )
        TENANT_GOVERNANCE.release(job_id)
    finally:
        if hasattr(_job_context, "job_id"):
            del _job_context.job_id


def finish_cancelled_after_publish(job_id: str, **fields: Any) -> None:
    """Close a job cancelled while TikTok was already receiving the video.

    The upload cannot be rolled back from here, so the job is marked cancelled
    with the publish result kept and a warning to verify it on TikTok. The
    rendering work was delivered, so the quota is consumed, not released.
    """

    current = JOB_SERVICE.get(job_id) or {}
    update_job(
        job_id,
        status="cancelled",
        step="cancelled",
        progress=float(current.get("progress", 0.99)),
        step_detail=TIKTOK_CANCEL_UNCERTAIN_DETAIL,
        finished_at=utc_now(),
        **fields,
    )
    TENANT_GOVERNANCE.consume(job_id)


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
        publish_result = tenant_tiktok_publisher(job_owner_user_id(job_id)).publish(
            video_path,
            title,
            privacy_level=privacy_level,
        )
        publish_fields = {
            "tiktok_publish_id": publish_result["publish_id"],
            "tiktok_publish_title": publish_result["title"],
            "tiktok_publish_privacy_level": publish_result["privacy_level"],
            "tiktok_publish_status": publish_result["status"],
            "tiktok_publish_detail": publish_result.get("status_payload"),
        }
        if JOB_SERVICE.is_cancel_requested(job_id):
            finish_cancelled_after_publish(
                job_id, job_action=None, publish_request=None, **publish_fields
            )
            return
        update_job(
            job_id,
            status="completed",
            step="completed",
            progress=1.0,
            step_detail="Đã gửi video lên TikTok theo lịch",
            finished_at=utc_now(),
            job_action=None,
            publish_request=None,
            **publish_fields,
        )
        TENANT_GOVERNANCE.consume(job_id)
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
        TENANT_GOVERNANCE.release(job_id)
    except Exception as exc:
        logger.warning("Scheduled TikTok publish failed for job %s", job_id, exc_info=True)
        if JOB_SERVICE.is_cancel_requested(job_id):
            update_job(
                job_id,
                status="cancelled",
                step="cancelled",
                step_detail="Đã hủy lịch đăng TikTok",
                finished_at=utc_now(),
                job_action=None,
                publish_request=None,
                tiktok_publish_status="FAILED",
                tiktok_publish_error=str(exc),
            )
            TENANT_GOVERNANCE.release(job_id)
            return
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
        TENANT_GOVERNANCE.consume(job_id)
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

    job = JOB_SERVICE.get(job_id) or {}
    owner_user_id = str(job.get("owner_user_id") or _bootstrap_owner["user_id"])
    upload_dir = tenant_job_dir(UPLOAD_DIR, owner_user_id, job_id)
    result = SOCIAL_VIDEO_DOWNLOADER.download(
        source_url,
        upload_dir,
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
        if source_cookie_path.is_relative_to(upload_dir):
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
        if job_action == COMIC_JOB_ACTION:
            run_comic_job(job_id, resume_request)
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
        source_language, target_language = languages_from_config(resume_request)
        process_video(
            job_id=job_id,
            video_path=video_path,
            mode=int(resume_request["mode"]),
            whisper_model=str(resume_request["whisper_model"]),
            voice_type=str(resume_request["voice_type"]),
            voice_mode=str(resume_request["voice_mode"]),
            voice_style=str(resume_request.get("voice_style", "natural")),
            tts_provider=str(resume_request.get("tts_provider", "edge")),
            translation_provider=str(resume_request.get("translation_provider", "google")),
            tts_voices=dict(resume_request.get("tts_voices") or {}),
            speech_rate=float(resume_request["speech_rate"]),
            background_music_path=background_music_path,
            keep_original_audio=bool(resume_request["keep_original_audio"]),
            original_audio_volume=float(resume_request["original_audio_volume"]),
            music_volume=float(resume_request["music_volume"]),
            subtitle_style=subtitle_style,
            subtitle_source=str(resume_request["subtitle_source"]),
            source_language=source_language,
            target_language=target_language,
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
        TENANT_GOVERNANCE.release(job_id)
    except (KeyError, TypeError, ValueError, FileNotFoundError, SocialVideoDownloadError) as exc:
        logger.warning("Unable to resume job %s: %s", job_id, exc)
        update_job(
            job_id,
            status="failed",
            step="failed",
            error=public_error_message(exc),
            finished_at=utc_now(),
        )


def comic_project_root(owner_user_id: str, job_id: str) -> Path:
    return tenant_job_dir(WORK_DIR, owner_user_id, job_id) / "comic"


def tiktok_post_description(post: TikTokPost | None) -> str:
    """TikTok's post_info.title is the post description: the full caption (body, CTA and
    hashtags), not the 80-character title that only labels the artifact."""
    if post is None:
        return ""
    return (post.caption or post.title).strip()


def synthesize_comic_speech(
    text: str,
    output_path: Path,
    gender: str,
    rate_percent: int,
    pitch_hz: int,
    language: str,
    provider: str = "edge",
    voice_key: str | None = None,
) -> None:
    if provider == "cit":
        # No Edge fallback: the part would be cached under the CIT voice's key. The job fails
        # with the reason instead and a retry reuses every image already generated.
        synthesize_cit(
            text, output_path, voice_type=gender, voice_key=voice_key,
            speed=1.0 + rate_percent / 100.0, language=language,
        )
        return
    voice_name = language_profile(language).edge_voice(gender)
    try:
        tts_edge_with_retry(text, output_path, voice_name, f"{rate_percent:+d}%", f"{pitch_hz:+d}Hz")
    except Exception as exc:
        logger.warning("edge TTS failed for comic narration, fallback to gTTS: %s", type(exc).__name__)
        tts_gtts_sync(text, output_path, language=language)


def comic_runtime(job_id: str, report: Callable[[str, float, str], None]) -> ComicRuntime:
    return ComicRuntime(
        ffmpeg=FFMPEG,
        run_ffmpeg=lambda args: run_ffmpeg(args, job_id=job_id),
        media_duration=get_media_duration,
        synthesize=synthesize_comic_speech,
        font_path=BUNDLED_FONT if BUNDLED_FONT.is_file() else None,
        check_active=lambda: ensure_job_active(job_id),
        report=report,
    )


COMIC_RUNNER = ComicJobRunner(
    get_job=lambda job_id: JOB_SERVICE.get(job_id),
    update_job=update_job,
    ensure_active=ensure_job_active,
    project_root=comic_project_root,
    output_dir=lambda owner_user_id, job_id: tenant_job_dir(OUTPUT_DIR, owner_user_id, job_id),
    artifact_name=stored_artifact_name,
    make_runtime=comic_runtime,
    font_path=BUNDLED_FONT if BUNDLED_FONT.is_file() else None,
    utc_now=utc_now,
)


def run_comic_job(job_id: str, request: dict[str, Any]) -> None:
    _job_context.job_id = job_id
    try:
        COMIC_RUNNER.run(job_id, request)
        TENANT_GOVERNANCE.consume(job_id)
    except JobCancelled:
        logger.info("Comic video cancelled for job %s", job_id)
        current = JOB_SERVICE.get(job_id) or {}
        update_job(
            job_id,
            status="cancelled",
            step="cancelled",
            progress=float(current.get("progress", 0.0)),
            step_detail="Đã hủy theo yêu cầu",
            finished_at=utc_now(),
        )
        TENANT_GOVERNANCE.release(job_id)
    except Exception as exc:
        logger.exception("Comic video failed for job %s", job_id)
        update_job(job_id, status="failed", step="failed", error=public_error_message(exc), finished_at=utc_now())
        TENANT_GOVERNANCE.release(job_id)
    finally:
        if hasattr(_job_context, "job_id"):
            del _job_context.job_id


def retention_days(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        logger.warning("Invalid %s; using %d days", name, default)
        return default


def retention_enabled() -> bool:
    return os.environ.get("VIDTRANS_RETENTION_ENABLED", "1").lower() in {"1", "true", "yes"}


def default_video_retention_days() -> int | None:
    """System-wide job/video retention, or None when automatic deletion is off."""
    return retention_days("VIDTRANS_JOB_RETENTION_DAYS", 30) if retention_enabled() else None


def remove_pruned_storage(removed: dict[str, Any]) -> None:
    for job in removed["jobs"]:
        for root in (UPLOAD_DIR, OUTPUT_DIR, WORK_DIR):
            path = tenant_storage_path(root, job["owner_user_id"], job["job_id"])
            if path.is_dir():
                shutil.rmtree(path)
    for batch in removed["batches"]:
        path = tenant_storage_path(UPLOAD_DIR, batch["owner_user_id"], batch["batch_id"])
        if path.is_dir():
            shutil.rmtree(path)


def apply_retention_policy() -> None:
    """Prune old terminal jobs per account policy, then the system-wide policy if enabled."""
    now = datetime.now(timezone.utc)
    overrides = ADMIN_ACCOUNTS.video_retention_overrides()
    account_removed = 0
    for user_id, days in overrides.items():
        if days <= 0:
            continue
        removed = JOB_SERVICE.prune_terminal(
            (now - timedelta(days=days)).isoformat(), owner_user_id=user_id
        )
        remove_pruned_storage(removed)
        account_removed += len(removed["jobs"])
    if account_removed:
        logger.info("Per-account video retention removed %d jobs", account_removed)
    if not retention_enabled():
        return
    job_cutoff = (now - timedelta(days=retention_days("VIDTRANS_JOB_RETENTION_DAYS", 30))).isoformat()
    audit_cutoff = (now - timedelta(days=retention_days("VIDTRANS_AUDIT_RETENTION_DAYS", 365))).isoformat()
    released_quota_cutoff = (
        now - timedelta(days=retention_days("VIDTRANS_RELEASED_QUOTA_RETENTION_DAYS", 90))
    ).isoformat()
    removed = JOB_SERVICE.prune_terminal(job_cutoff, exclude_owner_user_ids=overrides.keys())
    remove_pruned_storage(removed)
    governance_removed = TENANT_GOVERNANCE.prune(
        audit_cutoff=audit_cutoff,
        released_quota_cutoff=released_quota_cutoff,
    )
    logger.info(
        "Retention removed %d jobs, %d batches, %d audit events and %d released quota rows",
        len(removed["jobs"]), len(removed["batches"]), governance_removed["audit_events"],
        governance_removed["released_quota_rows"],
    )


async def run_retention_loop() -> None:
    try:
        interval = max(5, int(os.environ.get("VIDTRANS_RETENTION_INTERVAL_MINUTES", "60"))) * 60
    except ValueError:
        interval = 3600
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(apply_retention_policy)
        except Exception:
            logger.exception("Retention sweep failed")


@app.on_event("startup")
async def start_job_scheduler() -> None:
    await JOB_EVENTS.start()
    await SYSTEM_EVENTS.start()
    apply_retention_policy()
    app.state.retention_task = asyncio.create_task(run_retention_loop())
    if os.environ.get("VIDTRANS_RECOVER_INTERRUPTED_JOBS", "1").lower() in {"1", "true", "yes"}:
        interrupted = JOB_SERVICE.requeue_interrupted(SETTINGS.max_job_restarts)
        requeued = [job_id for job_id, job in interrupted if job["status"] == "queued"]
        if requeued:
            logger.info("Requeued %d interrupted jobs after server restart", len(requeued))
        for job_id, job in interrupted:
            if job["status"] == "failed":
                logger.warning(
                    "Job %s failed after %d server restarts; not requeuing it",
                    job_id,
                    job["restart_count"],
                )
                TENANT_GOVERNANCE.release(job_id)
        offset = 0
        while True:
            cancelled_jobs, total = JOB_SERVICE.list_jobs(
                status="cancelled", limit=500, offset=offset
            )
            for cancelled_job in cancelled_jobs:
                TENANT_GOVERNANCE.release(str(cancelled_job["job_id"]))
            offset += len(cancelled_jobs)
            if not cancelled_jobs or offset >= total:
                break
    logger.info("Running up to %d translation job(s) at once", SETTINGS.worker_concurrency)
    scheduler = JobScheduler(
        JOB_SERVICE,
        resume_process_video,
        concurrency=SETTINGS.worker_concurrency,
        on_cancelled=TENANT_GOVERNANCE.release,
    )
    app.state.job_scheduler = scheduler
    await scheduler.start()
    social_publish_scheduler = SocialPublishScheduler(SOCIAL_PUBLISHING)
    app.state.social_publish_scheduler = social_publish_scheduler
    await social_publish_scheduler.start()
    app.state.n8n_workflow_dispatcher = N8N_WORKFLOW_DISPATCHER
    await N8N_WORKFLOW_DISPATCHER.start()


@app.on_event("shutdown")
async def stop_job_scheduler() -> None:
    await JOB_EVENTS.stop()
    await SYSTEM_EVENTS.stop()
    retention_task = getattr(app.state, "retention_task", None)
    if retention_task is not None:
        retention_task.cancel()
    scheduler = getattr(app.state, "job_scheduler", None)
    if scheduler is not None:
        await scheduler.stop()
    social_publish_scheduler = getattr(app.state, "social_publish_scheduler", None)
    if social_publish_scheduler is not None:
        await social_publish_scheduler.stop()
    workflow_dispatcher = getattr(app.state, "n8n_workflow_dispatcher", None)
    if workflow_dispatcher is not None:
        await workflow_dispatcher.stop()

def notify_scheduler() -> None:
    scheduler = getattr(app.state, "job_scheduler", None)
    if scheduler is not None:
        scheduler.notify()


def notify_workflow_dispatcher() -> None:
    dispatcher = getattr(app.state, "n8n_workflow_dispatcher", None)
    if dispatcher is not None:
        dispatcher.notify()


def public_admin_account(account: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "user_id", "email", "display_name", "role", "approval_status", "plan",
        "payment_status", "approved_at", "created_at", "updated_at", "video_retention_days",
    }
    return {key: account.get(key) for key in allowed}


@app.get("/api/v1/admin/accounts/summary")
def admin_account_summary(request: Request) -> JSONResponse:
    require_roles(request, "owner", "admin")
    return JSONResponse(ADMIN_ACCOUNTS.summary(), headers={"Cache-Control": "no-store"})


@app.get("/api/v1/admin/accounts")
def list_admin_accounts(
    request: Request,
    query: str = Query(default="", max_length=120),
    status: str = Query(default="", max_length=32),
) -> JSONResponse:
    require_roles(request, "owner", "admin")
    try:
        items = []
        for account in ADMIN_ACCOUNTS.list(query=query, status=status):
            item = public_admin_account(account)
            item["quota"] = TENANT_GOVERNANCE.overview(str(account["user_id"]), account_plan_limit(account))
            items.append(item)
    except AdminAccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(
        {
            "items": items,
            "total": len(items),
            "video_retention": {"default_days": default_video_retention_days()},
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/v1/admin/accounts", status_code=201)
def create_admin_account(
    request: Request,
    email: str = Form(..., max_length=254),
    display_name: str = Form(..., max_length=120),
    password: str = Form(..., min_length=12, max_length=256),
    role: str = Form("viewer", max_length=32),
    plan: str = Form("free", max_length=32),
) -> JSONResponse:
    actor = require_roles(request, "owner", "admin")
    try:
        account = ADMIN_ACCOUNTS.create(
            email=email, display_name=display_name, role=role, plan=plan,
            approval_status="pending", payment_status="trial", password_hash=hash_password(password),
        )
        TENANT_GOVERNANCE.audit(
            actor_user_id=actor["user_id"],
            action="account.create",
            resource_type="account",
            resource_id=account["user_id"],
            target_user_id=account["user_id"],
            metadata={"email": email, "role": role, "plan": plan},
        )
    except AdminAccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(public_admin_account(account), status_code=201, headers={"Cache-Control": "no-store"})


@app.post("/api/v1/admin/accounts/{user_id}")
def update_admin_account(
    request: Request,
    user_id: str,
    display_name: str = Form(..., max_length=120),
    role: str = Form(..., max_length=32),
    approval_status: str = Form(..., max_length=32),
    plan: str = Form(..., max_length=32),
    payment_status: str = Form(..., max_length=32),
    video_retention_days: str | None = Form(None, max_length=16),
) -> JSONResponse:
    """``video_retention_days``: "default" follows the system policy, "0" never deletes."""
    actor = require_roles(request, "owner", "admin")
    changes: dict[str, Any] = {}
    if video_retention_days is not None:
        changes["video_retention_days"] = (
            None if video_retention_days.strip().lower() == "default" else video_retention_days.strip()
        )
    try:
        account = ADMIN_ACCOUNTS.update(
            user_id,
            display_name=display_name,
            role=role,
            approval_status=approval_status,
            plan=plan,
            payment_status=payment_status,
            **changes,
        )
        TENANT_GOVERNANCE.audit(
            actor_user_id=actor["user_id"],
            action="account.update",
            resource_type="account",
            resource_id=user_id,
            target_user_id=user_id,
            metadata={
                "display_name": display_name,
                "role": role,
                "approval_status": approval_status,
                "plan": plan,
                "payment_status": payment_status,
                "video_retention_days": account.get("video_retention_days"),
            },
        )
    except AdminAccountError as exc:
        status_code = 404 if "Không tìm thấy" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return JSONResponse(public_admin_account(account), headers={"Cache-Control": "no-store"})


@app.post("/api/v1/admin/accounts/{user_id}/password")
def reset_admin_account_password(
    request: Request,
    user_id: str,
    new_password: str = Form(..., min_length=12, max_length=256),
) -> JSONResponse:
    """Set a new password for a user and sign them out of every existing session."""
    actor = require_roles(request, "owner", "admin")
    try:
        target = ADMIN_ACCOUNTS.get(user_id)
    except AdminAccountError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if (
        actor["role"] != "owner"
        and target["user_id"] != actor["user_id"]
        and target["role"] in {"owner", "admin"}
    ):
        raise HTTPException(status_code=403, detail="Chỉ chủ sở hữu mới được đặt lại mật khẩu của quản trị viên")
    try:
        ADMIN_ACCOUNTS.set_password_hash(user_id, hash_password(new_password))
    except (AdminAccountError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    TENANT_GOVERNANCE.audit(
        actor_user_id=actor["user_id"],
        action="account.password_reset",
        resource_type="account",
        resource_id=user_id,
        target_user_id=user_id,
        metadata={"email": target["email"]},
    )
    return JSONResponse(
        {"status": "success", "user_id": user_id, "message": "Đã đặt lại mật khẩu; các phiên đăng nhập cũ đã bị thu hồi"},
        headers={"Cache-Control": "no-store"},
    )


@app.delete("/api/v1/admin/accounts/{user_id}")
def delete_admin_account_endpoint(request: Request, user_id: str) -> JSONResponse:
    caller = require_roles(request, "owner", "admin")
    if user_id == caller["user_id"]:
        raise HTTPException(status_code=400, detail="Không thể tự xóa tài khoản của chính mình")
    try:
        deleted = ADMIN_ACCOUNTS.delete(user_id)
    except AdminAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    TENANT_GOVERNANCE.audit(
        actor_user_id=caller["user_id"],
        action="account.delete",
        resource_type="account",
        resource_id=user_id,
        target_user_id=user_id,
        metadata={"email": deleted.get("email")},
    )
    PLATFORM_APP_STORE.delete_user(user_id)
    # Safely clean up user-specific storage directories
    for base_dir in (WORK_DIR, UPLOAD_DIR, OUTPUT_DIR):
        user_dir = base_dir / "users" / storage_owner_segment(user_id)
        if user_dir.exists() and user_dir.is_relative_to(base_dir.resolve()):
            shutil.rmtree(user_dir, ignore_errors=True)
    return JSONResponse({"deleted": True, "user_id": user_id}, headers={"Cache-Control": "no-store"})


def live_user_publishers(user_id: str, platform: str) -> list[Any]:
    """The user's cached publishers, so new credentials apply without restart."""
    key = platform.upper()
    with _TENANT_PUBLISHER_LOCK:
        if key == "TIKTOK":
            publisher = _TENANT_TIKTOK_PUBLISHERS.get(user_id)
            return [publisher] if publisher is not None else []
        registry = _TENANT_SOCIAL_REGISTRIES.get(user_id)
        return [registry.get(key)] if registry is not None else []


def refresh_user_platform_app(user_id: str, platform: str) -> dict[str, Any]:
    config = platform_app_config(platform, user_id)
    for publisher in live_user_publishers(user_id, platform):
        publisher.config = config
    return PLATFORM_APP_STORE.describe(user_id, platform, config)


@app.get("/api/v1/me/platform-apps")
def list_my_platform_apps(request: Request) -> JSONResponse:
    user_id = str(current_account(request)["user_id"])
    items = [
        PLATFORM_APP_STORE.describe(user_id, platform, platform_app_config(platform, user_id))
        for platform in PLATFORM_APPS
    ]
    return JSONResponse({"items": items}, headers={"Cache-Control": "no-store"})


@app.post("/api/v1/me/platform-apps/{platform}")
def save_my_platform_app(
    request: Request,
    platform: str,
    client_id: str = Form(..., max_length=256),
    client_secret: str = Form("", max_length=512),
    redirect_uri: str = Form(..., max_length=512),
) -> JSONResponse:
    user_id = str(current_account(request)["user_id"])
    key = platform.upper()
    try:
        PLATFORM_APP_STORE.save(
            user_id, key, client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri,
        )
    except PlatformAppError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(refresh_user_platform_app(user_id, key), headers={"Cache-Control": "no-store"})


@app.delete("/api/v1/me/platform-apps/{platform}")
def delete_my_platform_app(request: Request, platform: str) -> JSONResponse:
    user_id = str(current_account(request)["user_id"])
    key = platform.upper()
    try:
        PLATFORM_APP_STORE.delete(user_id, key)
    except PlatformAppError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(refresh_user_platform_app(user_id, key), headers={"Cache-Control": "no-store"})


@app.get("/api/v1/admin/jobs")
def admin_list_jobs(
    request: Request,
    user_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    caller = require_roles(request, "owner", "admin")
    TENANT_GOVERNANCE.audit(
        actor_user_id=caller["user_id"],
        action="admin.jobs.list",
        resource_type="job",
        target_user_id=user_id or None,
        metadata={"user_id": user_id, "status": status, "search": search, "limit": limit, "offset": offset},
    )
    jobs, total = JOB_SERVICE.list_jobs(
        status=status,
        search=search,
        limit=limit,
        offset=offset,
        owner_user_id=user_id or None,
    )
    positions = JOB_SERVICE.queue_positions()
    user_lookup: dict[str, dict[str, str]] = {}
    items = []
    for job in jobs:
        owner = str(job.get("owner_user_id") or "")
        if owner and owner not in user_lookup:
            try:
                acc = ADMIN_ACCOUNTS.get(owner)
                user_lookup[owner] = {"email": acc.get("email", ""), "display_name": acc.get("display_name", "")}
            except AdminAccountError:
                user_lookup[owner] = {"email": owner, "display_name": owner}
        user_info = user_lookup.get(owner, {"email": owner, "display_name": owner})
        job_data = job_response(str(job["job_id"]), job, positions)
        job_data["owner_user_id"] = owner
        job_data["owner_email"] = user_info["email"]
        job_data["owner_display_name"] = user_info["display_name"]
        items.append(job_data)
    return JSONResponse({"items": items, "total": total, "limit": limit, "offset": offset}, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/admin/jobs/{job_id}")
def admin_get_job(request: Request, job_id: str) -> JSONResponse:
    caller = require_roles(request, "owner", "admin")
    job = JOB_SERVICE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy job")
    owner = str(job.get("owner_user_id") or "")
    TENANT_GOVERNANCE.audit(
        actor_user_id=caller["user_id"],
        action="admin.jobs.view",
        resource_type="job",
        resource_id=job_id,
        target_user_id=owner or None,
        metadata={"job_id": job_id},
    )
    user_info = {"email": owner, "display_name": owner}
    if owner:
        try:
            acc = ADMIN_ACCOUNTS.get(owner)
            user_info = {"email": acc.get("email", ""), "display_name": acc.get("display_name", "")}
        except AdminAccountError:
            pass
    job_data = job_response(job_id, job, JOB_SERVICE.queue_positions())
    job_data["owner_user_id"] = owner
    job_data["owner_email"] = user_info["email"]
    job_data["owner_display_name"] = user_info["display_name"]
    return JSONResponse(job_data, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/admin/audit-events")
def admin_list_audit_events(
    request: Request,
    actor_user_id: str | None = Query(default=None),
    target_user_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    require_roles(request, "owner", "admin")
    items, total = TENANT_GOVERNANCE.list_audit(
        actor_user_id=actor_user_id or None,
        target_user_id=target_user_id or None,
        action=action or None,
        limit=limit,
        offset=offset,
    )
    return JSONResponse({"items": items, "total": total, "limit": limit, "offset": offset}, headers={"Cache-Control": "no-store"})


SYSTEM_METRICS = SystemMetricsCollector(
    disk_paths={"Dữ liệu (work)": WORK_DIR, "Kết quả (outputs)": OUTPUT_DIR},
    ollama_url=ollama_url,
)


# The production compose file mounts the host /proc read-only here so the
# monitor can see Ollama, browsers and databases outside this container.
_HOST_PROC = Path(os.environ.get("VIDTRANS_HOST_PROC", "/host/proc"))
PROCESS_METRICS = HostProcessCollector(
    proc_root=_HOST_PROC if (_HOST_PROC / "meminfo").exists() else Path("/proc"),
)
PROCESS_AUDIT_INTERVAL_SECONDS = 600
_process_audit_lock = threading.Lock()
_process_audit_last: dict[str, float] = {}


def require_signed_in_admin(request: Request) -> dict[str, Any]:
    """Admin role from a verified session, never the auth-disabled fallback.

    Host metrics describe the whole server, so unlike other admin pages they
    are refused when authentication is turned off instead of being served to
    whoever can reach the port.
    """

    if getattr(request.state, "account", None) is None:
        raise HTTPException(status_code=403, detail="Cần đăng nhập bằng tài khoản quản trị để xem thông tin máy chủ")
    return require_roles(request, "owner", "admin")


def collect_system_metrics() -> dict[str, Any]:
    metrics = SYSTEM_METRICS.collect()
    active_jobs, _total = JOB_SERVICE.list_jobs(status="processing", limit=20)
    metrics["jobs"] = {
        "counts": JOB_SERVICE.status_counts(),
        "worker_concurrency": SETTINGS.worker_concurrency,
        "max_job_restarts": SETTINGS.max_job_restarts,
        "active": [
            {
                "job_id": job.get("job_id"),
                "filename": job.get("filename"),
                "step": job.get("step"),
                "step_detail": job.get("step_detail"),
                "progress": job.get("progress"),
                "restart_count": job.get("restart_count", 0),
                "started_at": job.get("started_at"),
            }
            for job in active_jobs
        ],
    }
    return metrics


SYSTEM_PROCESS_LIMIT = 15
SYSTEM_EVENTS = SystemMetricsHub(
    lambda: {
        "type": "system",
        "metrics": collect_system_metrics(),
        "processes": PROCESS_METRICS.collect(SYSTEM_PROCESS_LIMIT),
    },
    interval=5.0,
)


async def audit_process_view(actor_id: str) -> None:
    now = time.monotonic()
    with _process_audit_lock:
        should_audit = now - _process_audit_last.get(actor_id, float("-inf")) >= PROCESS_AUDIT_INTERVAL_SECONDS
        if should_audit:
            _process_audit_last[actor_id] = now
    if should_audit:
        # The page refreshes every few seconds; one audit row per viewing session
        # is enough to know who looked at the server's process table.
        await asyncio.to_thread(
            TENANT_GOVERNANCE.audit,
            actor_user_id=actor_id,
            action="admin.system.processes.view",
            resource_type="system",
            resource_id="processes",
        )


@app.get("/api/v1/admin/system-metrics")
async def admin_system_metrics(request: Request) -> JSONResponse:
    require_signed_in_admin(request)
    metrics = await asyncio.to_thread(collect_system_metrics)
    return JSONResponse(metrics, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/admin/system-processes")
async def admin_system_processes(
    request: Request,
    limit: int = Query(default=15, ge=1, le=50),
) -> JSONResponse:
    actor = require_signed_in_admin(request)
    await audit_process_view(str(actor["user_id"]))
    result = await asyncio.to_thread(PROCESS_METRICS.collect, limit)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/admin/users/{user_id}/quota")
def admin_user_quota(request: Request, user_id: str) -> JSONResponse:
    caller = require_roles(request, "owner", "admin")
    try:
        target_account = ADMIN_ACCOUNTS.get(user_id)
    except AdminAccountError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng") from exc
    limit = account_plan_limit(target_account)
    overview = TENANT_GOVERNANCE.overview(user_id, limit)
    TENANT_GOVERNANCE.audit(
        actor_user_id=caller["user_id"],
        action="admin.quota.view",
        resource_type="quota",
        resource_id=user_id,
        target_user_id=user_id,
        metadata={"plan": target_account.get("plan")},
    )
    return JSONResponse({"account": public_admin_account(target_account), "quota": overview}, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/admin/workflow-events/summary")
def workflow_event_summary() -> JSONResponse:
    return JSONResponse(
        {
            "enabled": N8N_WORKFLOW_CONFIG.enabled,
            "webhook_configured": bool(N8N_WORKFLOW_CONFIG.webhook_url),
            "counts": WORKFLOW_EVENT_STORE.summary(),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/v1/admin/workflow-events")
def list_workflow_events(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    try:
        items, total = WORKFLOW_EVENT_STORE.list_events(
            status=status or None,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(
        {"items": items, "total": total, "limit": limit, "offset": offset},
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/v1/admin/workflow-events/{event_id}/replay")
def replay_workflow_event(event_id: str) -> JSONResponse:
    if not N8N_WORKFLOW_CONFIG.enabled:
        raise HTTPException(status_code=409, detail="Tích hợp n8n chưa được cấu hình")
    current = WORKFLOW_EVENT_STORE.get(event_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy workflow event")
    if not WORKFLOW_EVENT_STORE.replay_dead(event_id):
        raise HTTPException(status_code=409, detail="Chỉ event dead mới có thể chạy lại")
    notify_workflow_dispatcher()
    return JSONResponse(
        {"event_id": event_id, "status": "pending"},
        status_code=202,
        headers={"Cache-Control": "no-store"},
    )


def validate_tiktok_auto_publish(request: ProcessingRequest, owner_user_id: str) -> None:
    if not request.tiktok.auto_publish:
        return
    publisher = tenant_tiktok_publisher(owner_user_id)
    if not publisher.connection_status()["connected"]:
        raise HTTPException(status_code=400, detail="Hãy kết nối tài khoản TikTok trước khi bật tự động đăng")
    try:
        creator = publisher.creator_info()
    except (TikTokPublisherError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    privacy_options = creator.get("privacy_level_options") or []
    if request.tiktok.privacy_level not in privacy_options:
        raise HTTPException(
            status_code=400,
            detail="Tài khoản TikTok không hỗ trợ mức quyền riêng tư đã chọn",
        )


@app.get("/api/v1/me")
def get_current_user(request: Request) -> JSONResponse:
    account = current_account(request)
    return JSONResponse(public_admin_account(account), headers={"Cache-Control": "no-store"})


@app.get("/api/v1/billing/overview")
def billing_overview(request: Request) -> JSONResponse:
    account = current_account(request)
    providers = PAYMENT_CONFIG.availability()
    quota_info = TENANT_GOVERNANCE.overview(str(account["user_id"]), account_plan_limit(account))
    return JSONResponse(
        {
            "account": public_admin_account(account),
            "quota": quota_info,
            "plans": [{"key": key, **value} for key, value in PLAN_CATALOG.items()],
            "providers": providers,
            "provider_notes": {
                "bank": "Chuyển khoản/VietQR — chờ quản trị viên đối soát",
                "momo": "MoMo — cần merchant sandbox và callback HTTPS",
                "zalopay": "ZaloPay — cần merchant sandbox và callback HTTPS",
            },
            "orders": PAYMENT_ORDERS.list_for_user(account["user_id"]),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/v1/billing/orders", status_code=201)
def create_payment_order(
    request: Request,
    plan: str = Form(..., max_length=32),
    provider: str = Form(..., max_length=32),
) -> JSONResponse:
    account = current_account(request)
    if not PAYMENT_CONFIG.availability().get(provider, False):
        raise HTTPException(status_code=409, detail="Phương thức thanh toán chưa được cấu hình")
    try:
        order = PAYMENT_ORDERS.create(user_id=account["user_id"], provider=provider, plan=plan)
    except PaymentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if provider == "bank":
        order["bank"] = {
            "name": PAYMENT_CONFIG.bank_name,
            "bin": PAYMENT_CONFIG.bank_bin,
            "account_no": PAYMENT_CONFIG.bank_account_no,
            "account_name": PAYMENT_CONFIG.bank_account_name,
            "qr_url": PAYMENT_CONFIG.bank_qr_url(order),
        }
    else:
        try:
            gateway = (
                PAYMENT_GATEWAY.create_momo(order)
                if provider == "momo"
                else PAYMENT_GATEWAY.create_zalopay(order, account["user_id"])
            )
            PAYMENT_ORDERS.set_provider_order_ref(order["order_id"], gateway["provider_order_ref"])
            order["checkout_url"] = gateway["checkout_url"]
        except PaymentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(order, status_code=201, headers={"Cache-Control": "no-store"})


def activate_paid_order(order: dict[str, Any]) -> None:
    ADMIN_ACCOUNTS.update(order["user_id"], plan=order["plan"], payment_status="active")


@app.post("/api/v1/billing/callbacks/bank")
async def bank_payment_callback(request: Request) -> Response:
    raw_payload = await request.body()
    signature = request.headers.get("x-vidtrans-signature", "")
    if not PAYMENT_GATEWAY.verify_bank_callback(raw_payload, signature):
        raise HTTPException(status_code=400, detail="Chữ ký đối soát ngân hàng không hợp lệ")
    try:
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            raise PaymentError("Payload đối soát ngân hàng không hợp lệ")
        order_id = str(payload.get("order_id", ""))
        transaction_id = str(payload.get("transaction_id", ""))
        amount = int(payload.get("amount", -1))
        order = PAYMENT_ORDERS.get(order_id)
        if order["provider"] != "bank" or amount != order["amount"]:
            raise PaymentError("Thông tin chuyển khoản không khớp")
        if not transaction_id or len(transaction_id) > 200:
            raise PaymentError("Mã giao dịch ngân hàng không hợp lệ")
        order, applied = PAYMENT_ORDERS.mark_paid(
            order_id,
            event_key=f"bank:{transaction_id}",
            payload=raw_payload,
            provider_transaction_ref=transaction_id,
        )
        if applied:
            activate_paid_order(order)
    except (json.JSONDecodeError, PaymentError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(status_code=204)


@app.post("/api/v1/billing/callbacks/momo")
async def momo_payment_callback(request: Request) -> Response:
    payload = await request.json()
    if not isinstance(payload, dict) or not PAYMENT_GATEWAY.verify_momo_callback(payload):
        raise HTTPException(status_code=400, detail="Chữ ký MoMo không hợp lệ")
    try:
        order = PAYMENT_ORDERS.get(str(payload.get("orderId", "")))
        if order["provider"] != "momo" or int(payload.get("amount", -1)) != order["amount"]:
            raise PaymentError("Thông tin đơn MoMo không khớp")
        if int(payload.get("resultCode", -1)) == 0:
            order, applied = PAYMENT_ORDERS.mark_paid(
                order["order_id"],
                event_key=f"momo:{payload.get('transId')}:{order['order_id']}",
                payload=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                provider_transaction_ref=str(payload.get("transId", "")),
            )
            if applied:
                activate_paid_order(order)
    except (PaymentError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(status_code=204)


@app.post("/api/v1/billing/callbacks/zalopay")
async def zalopay_payment_callback(request: Request) -> JSONResponse:
    payload = await request.json()
    data = payload.get("data", "") if isinstance(payload, dict) else ""
    mac = payload.get("mac", "") if isinstance(payload, dict) else ""
    if not isinstance(data, str) or not PAYMENT_GATEWAY.verify_zalopay_callback(data, str(mac)):
        return JSONResponse({"return_code": 2, "return_message": "Invalid"})
    try:
        callback = json.loads(data)
        order = PAYMENT_ORDERS.get_by_provider_ref("zalopay", str(callback.get("app_trans_id", "")))
        if int(callback.get("amount", -1)) != order["amount"]:
            raise PaymentError("Thông tin đơn ZaloPay không khớp")
        order, applied = PAYMENT_ORDERS.mark_paid(
            order["order_id"],
            event_key=f"zalopay:{callback.get('zp_trans_id')}:{order['order_id']}",
            payload=data.encode("utf-8"),
            provider_transaction_ref=str(callback.get("zp_trans_id", "")),
        )
        if applied:
            activate_paid_order(order)
    except (PaymentError, TypeError, ValueError, json.JSONDecodeError):
        return JSONResponse({"return_code": 2, "return_message": "Invalid"})
    return JSONResponse({"return_code": 1, "return_message": "Success"})


@app.get("/api/v1/admin/billing/orders")
def list_billing_orders_for_admin(
    request: Request,
    status: str | None = Query(default=None, max_length=16),
    awaiting_review: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    actor = require_roles(request, "owner", "admin")
    try:
        items, total = PAYMENT_ORDERS.list_orders(
            status=status or None, awaiting_review=awaiting_review, limit=limit, offset=offset
        )
    except PaymentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    TENANT_GOVERNANCE.audit(
        actor_user_id=actor["user_id"],
        action="admin.billing.orders.list",
        resource_type="order",
        metadata={"status": status or "", "awaiting_review": awaiting_review},
    )
    return JSONResponse(
        {"items": items, "total": total, "limit": limit, "offset": offset},
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/v1/admin/billing/orders/{order_id}/reject")
def reject_bank_payment(
    request: Request, order_id: str, reason: str = Form("", max_length=200)
) -> JSONResponse:
    actor = require_roles(request, "owner", "admin")
    try:
        updated = PAYMENT_ORDERS.cancel(order_id)
    except PaymentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    TENANT_GOVERNANCE.audit(
        actor_user_id=actor["user_id"],
        action="billing.order_reject",
        resource_type="order",
        resource_id=order_id,
        target_user_id=updated.get("user_id"),
        metadata={"order_id": order_id, "plan": updated.get("plan"), "reason": reason.strip()},
    )
    return JSONResponse(updated, headers={"Cache-Control": "no-store"})


@app.post("/api/v1/admin/billing/orders/{order_id}/confirm")
def confirm_bank_payment(request: Request, order_id: str) -> JSONResponse:
    actor = require_roles(request, "owner", "admin")
    try:
        order = PAYMENT_ORDERS.get(order_id)
        if order["provider"] != "bank":
            raise PaymentError("Chỉ đơn chuyển khoản được xác nhận thủ công")
        updated, _ = PAYMENT_ORDERS.mark_paid(
            order_id,
            event_key=f"bank-confirm:{order_id}",
            payload=f"confirmed-by:{actor['user_id']}".encode("utf-8"),
            provider_transaction_ref="manual-bank-confirmation",
        )
        activate_paid_order(updated)
        TENANT_GOVERNANCE.audit(
            actor_user_id=actor["user_id"],
            action="billing.order_confirm",
            resource_type="order",
            resource_id=order_id,
            target_user_id=order.get("user_id"),
            metadata={"order_id": order_id, "plan": updated.get("plan")},
        )
    except PaymentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(updated, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/billing/orders/{order_id}")
def get_billing_order_detail(request: Request, order_id: str) -> JSONResponse:
    account = current_account(request)
    try:
        order = PAYMENT_ORDERS.get(order_id)
        if account["role"] not in {"owner", "admin"} and order["user_id"] != account["user_id"]:
            raise HTTPException(status_code=403, detail="Không có quyền xem đơn thanh toán này")
        if order["provider"] == "bank":
            order["bank"] = {
                "name": PAYMENT_CONFIG.bank_name,
                "bin": PAYMENT_CONFIG.bank_bin,
                "account_no": PAYMENT_CONFIG.bank_account_no,
                "account_name": PAYMENT_CONFIG.bank_account_name,
                "qr_url": PAYMENT_CONFIG.bank_qr_url(order),
            }
    except PaymentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(order, headers={"Cache-Control": "no-store"})


@app.post("/api/v1/billing/orders/{order_id}/confirm")
def confirm_billing_order(request: Request, order_id: str) -> JSONResponse:
    """The customer reports a bank transfer; the order stays pending until reconciled.

    Only a signed provider callback or an owner/admin confirmation may mark an order paid
    (URD rbac-customer-billing §IV), so this never activates a plan.
    """
    account = current_account(request)
    try:
        updated = PAYMENT_ORDERS.report_transfer(order_id, user_id=account["user_id"])
    except PaymentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(updated, headers={"Cache-Control": "no-store"})


@app.post("/api/v1/billing/orders/{order_id}/cancel")
def cancel_billing_order(request: Request, order_id: str) -> JSONResponse:
    actor = current_account(request)
    try:
        # Admins reject other users' orders through the audited admin endpoint.
        updated = PAYMENT_ORDERS.cancel(order_id, user_id=actor["user_id"])
    except PaymentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(updated, headers={"Cache-Control": "no-store"})


@app.post("/api/v1/billing/switch-free")
def switch_free_plan(request: Request) -> JSONResponse:
    account = current_account(request)
    ADMIN_ACCOUNTS.update(account["user_id"], plan="free", payment_status="exempt")
    return JSONResponse({"status": "success", "plan": "free"}, headers={"Cache-Control": "no-store"})



REFRESH_COOKIE_PATH = "/api/v1/auth/"


def token_predates_password_change(claims: dict[str, Any], account: dict[str, Any]) -> bool:
    """A password reset invalidates every access and refresh token issued before it."""
    changed_at = account.get("password_changed_at")
    if not changed_at:
        return False
    try:
        changed_timestamp = int(datetime.fromisoformat(str(changed_at)).timestamp())
    except ValueError:
        return False
    return int(claims["iat"]) < changed_timestamp


def account_token_scope(account: dict[str, Any]) -> str:
    return "admin" if account["role"] in {"owner", "admin"} else "user"


def issue_refresh_token(account: dict[str, Any], *, remember: bool) -> tuple[str, int]:
    return AUTH_MANAGER.issue_token(
        account["email"],
        user_id=account["user_id"],
        role=account["role"],
        scope=account_token_scope(account),
        token_use="refresh",
        remember=remember,
    )


# Native apps (Flutter) keep tokens in secure storage instead of cookies: they ask for
# client=native at login and send the refresh token in the body of /auth/refresh.
AUTH_CLIENT_TYPES = {"web", "native"}


def set_auth_cookies(
    response: Response,
    account: dict[str, Any],
    access_token: str,
    access_expires_in: int,
    *,
    remember: bool,
) -> None:
    """Set the access cookie and a refresh cookie scoped to the auth endpoints.

    Without "remember me" both are session cookies that end with the browser.
    """
    refresh_token, refresh_expires_in = issue_refresh_token(account, remember=remember)
    cookie_options = {
        "httponly": True,
        "secure": AUTH_MANAGER.config.cookie_secure,
        "samesite": "strict",
    }
    response.set_cookie(
        key=AUTH_MANAGER.config.cookie_name,
        value=access_token,
        max_age=access_expires_in if remember else None,
        path="/",
        **cookie_options,
    )
    response.set_cookie(
        key=AUTH_MANAGER.config.refresh_cookie_name,
        value=refresh_token,
        max_age=refresh_expires_in if remember else None,
        path=REFRESH_COOKIE_PATH,
        **cookie_options,
    )


def clear_auth_cookies(response: Response) -> None:
    for key, path in (
        (AUTH_MANAGER.config.cookie_name, "/"),
        (AUTH_MANAGER.config.refresh_cookie_name, REFRESH_COOKIE_PATH),
    ):
        response.delete_cookie(
            key=key,
            path=path,
            secure=AUTH_MANAGER.config.cookie_secure,
            httponly=True,
            samesite="strict",
        )


@app.get("/api/v1/auth/status")
def get_auth_status(request: Request) -> JSONResponse:
    payload = AUTH_MANAGER.status()
    payload["authenticated"] = False
    payload["username"] = None
    payload["role"] = None
    payload["user_id"] = None
    if AUTH_MANAGER.config.configured:
        token, _, malformed_header = request_access_token(request)
        if token and not malformed_header:
            try:
                claims = AUTH_MANAGER.verify_token(token)
                payload["authenticated"] = True
                payload["username"] = claims["sub"]
                payload["role"] = claims["role"]
                payload["user_id"] = claims["user_id"]
            except (AuthConfigurationError, InvalidTokenError):
                pass
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.post("/api/v1/auth/token")
def create_auth_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    remember: bool = Form(default=False),
    client: str = Form(default="web"),
) -> JSONResponse:
    if client not in AUTH_CLIENT_TYPES:
        raise HTTPException(status_code=422, detail="client phải là web hoặc native")
    client_id = auth_client_id(request)
    account = ADMIN_ACCOUNTS.get_by_email(form_data.username)
    if account is None and form_data.username == AUTH_MANAGER.config.username:
        account = ADMIN_ACCOUNTS.get_by_email(
            form_data.username if "@" in form_data.username else f"{form_data.username}@local.vidtrans"
        )
    try:
        if account and account.get("password_hash"):
            authenticated = AUTH_MANAGER.authenticate_password(
                form_data.password, str(account["password_hash"]), client_id
            )
        else:
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
    if account is None or account["approval_status"] != "approved":
        raise HTTPException(status_code=403, detail="Tài khoản chưa được phê duyệt hoặc đã bị khóa")
    scope = account_token_scope(account)
    token, expires_in = AUTH_MANAGER.issue_token(
        account["email"], user_id=account["user_id"], role=account["role"], scope=scope
    )
    payload = {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "scope": scope,
        "role": account["role"],
        "remember": remember,
    }
    if client == "native":
        refresh_token, refresh_expires_in = issue_refresh_token(account, remember=remember)
        payload.update(refresh_token=refresh_token, refresh_expires_in=refresh_expires_in)
        return JSONResponse(payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    response = JSONResponse(payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    set_auth_cookies(response, account, token, expires_in, remember=remember)
    return response


class RefreshTokenBody(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


@app.post("/api/v1/auth/refresh", openapi_extra={"requestBody": {
    "required": False,
    "description": "Native apps only. Web clients send no body and use the refresh cookie.",
    "content": {"application/json": {"schema": RefreshTokenBody.model_json_schema()}},
}})
async def refresh_auth_token(request: Request) -> JSONResponse:
    """Exchange a refresh token for a new short-lived access token.

    Web: the HttpOnly refresh cookie plus the anti-CSRF header. Native apps: a JSON body
    ``{"refresh_token": ...}``; nothing ambient is sent, so no CSRF header is needed.
    """

    if not AUTH_MANAGER.config.configured:
        raise HTTPException(status_code=503, detail="Xác thực đã bật nhưng cấu hình máy chủ chưa hợp lệ")
    body_token = None
    if request.headers.get("content-type", "").split(";")[0].strip() == "application/json":
        try:
            body_token = RefreshTokenBody.model_validate(await request.json()).refresh_token
        except (ValueError, ValidationError):
            return JSONResponse({"detail": "Body phải có refresh_token"}, status_code=422, headers={"Cache-Control": "no-store"})
    native = body_token is not None
    if not native and request.headers.get("x-vidtrans-request") != "1":
        return JSONResponse({"detail": "Thiếu tiêu đề chống CSRF"}, status_code=403, headers={"Cache-Control": "no-store"})
    refresh_token = body_token or request.cookies.get(AUTH_MANAGER.config.refresh_cookie_name)
    if not refresh_token:
        return unauthorized_response()

    def rejected(response: JSONResponse) -> JSONResponse:
        if not native:
            clear_auth_cookies(response)
        return response

    try:
        claims = await asyncio.to_thread(AUTH_MANAGER.verify_token, refresh_token, token_use="refresh")
        account = await asyncio.to_thread(ADMIN_ACCOUNTS.get, str(claims["user_id"]))
    except (AuthConfigurationError, InvalidTokenError, AdminAccountError, KeyError):
        return rejected(unauthorized_response())
    if account["approval_status"] != "approved":
        return rejected(JSONResponse({"detail": "Tài khoản chưa được phê duyệt hoặc đã bị khóa"}, status_code=403))
    if token_predates_password_change(claims, account):
        return rejected(unauthorized_response("Mật khẩu đã được thay đổi. Hãy đăng nhập lại."))
    scope = account_token_scope(account)
    token, expires_in = AUTH_MANAGER.issue_token(
        account["email"], user_id=account["user_id"], role=account["role"], scope=scope
    )
    remember = bool(claims.get("remember"))
    if native:
        # The refresh token keeps its original expiry; the app keeps using the one it has.
        return JSONResponse(
            {"access_token": token, "token_type": "bearer", "expires_in": expires_in, "scope": scope,
             "role": account["role"], "remember": remember},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    response = JSONResponse(
        {"token_type": "bearer", "expires_in": expires_in, "scope": scope, "role": account["role"], "remember": remember},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
    # The refresh token keeps its original expiry, so a session lasts at most
    # VIDTRANS_REFRESH_TOKEN_TTL_DAYS after the password login.
    response.set_cookie(
        key=AUTH_MANAGER.config.cookie_name,
        value=token,
        max_age=expires_in if remember else None,
        httponly=True,
        secure=AUTH_MANAGER.config.cookie_secure,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/api/v1/auth/register", status_code=201)
def register_account(
    display_name: str = Form(..., min_length=1, max_length=120),
    email: str = Form(..., max_length=254),
    password: str = Form(..., min_length=12, max_length=256),
) -> JSONResponse:
    if not AUTH_MANAGER.config.configured:
        raise HTTPException(status_code=503, detail="Hệ thống đăng ký chưa sẵn sàng")
    try:
        account = ADMIN_ACCOUNTS.create(
            email=email,
            display_name=display_name,
            role="editor",
            approval_status="approved",
            plan="free",
            payment_status="exempt",
            password_hash=hash_password(password),
        )
    except (AdminAccountError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    token, expires_in = AUTH_MANAGER.issue_token(
        account["email"], user_id=account["user_id"], role=account["role"], scope="user"
    )
    response = JSONResponse(
        {"account": public_admin_account(account), "expires_in": expires_in},
        status_code=201,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
    set_auth_cookies(response, account, token, expires_in, remember=False)
    return response


PASSWORD_RESET_GENERIC_MESSAGE = (
    "Nếu email đã đăng ký, mã xác nhận gồm 6 chữ số đã được gửi tới hộp thư. "
    f"Mã có hiệu lực {CODE_TTL_SECONDS // 60} phút."
)
PASSWORD_RESET_INVALID_CODE = "Mã xác nhận không đúng hoặc đã hết hạn"
PASSWORD_RESET_IP_LIMIT = 10
PASSWORD_RESET_IP_WINDOW_SECONDS = 15 * 60
_password_reset_requests: dict[str, list[float]] = {}
_password_reset_requests_lock = threading.Lock()


def check_password_reset_rate_limit(client_id: str) -> None:
    """Bound reset traffic per client; per-account limits live in the reset store."""
    now = time.monotonic()
    with _password_reset_requests_lock:
        recent = [
            stamp for stamp in _password_reset_requests.get(client_id, [])
            if stamp > now - PASSWORD_RESET_IP_WINDOW_SECONDS
        ]
        if len(recent) >= PASSWORD_RESET_IP_LIMIT:
            _password_reset_requests[client_id] = recent
            retry_after = max(1, int(PASSWORD_RESET_IP_WINDOW_SECONDS - (now - recent[0])))
            raise HTTPException(
                status_code=429,
                detail="Bạn đã thử quá nhiều lần. Hãy thử lại sau ít phút.",
                headers={"Retry-After": str(retry_after)},
            )
        recent.append(now)
        _password_reset_requests[client_id] = recent


def require_password_reset_available() -> None:
    if not AUTH_MANAGER.config.configured:
        raise HTTPException(status_code=503, detail="Hệ thống đăng nhập chưa sẵn sàng")
    if not MAILER.configured:
        raise HTTPException(status_code=503, detail="Chức năng quên mật khẩu chưa được cấu hình gửi email")


def send_password_reset_email(email: str, code: str) -> None:
    minutes = CODE_TTL_SECONDS // 60
    try:
        MAILER.send(
            to=email,
            subject="[VidTrans] Mã xác nhận đặt lại mật khẩu",
            text=(
                "Xin chào,\n\n"
                "VidTrans đã nhận được yêu cầu đặt lại mật khẩu cho tài khoản của bạn. "
                "Vui lòng sử dụng mã xác nhận dưới đây để tiếp tục:\n\n"
                f"MÃ XÁC NHẬN: {code}\n\n"
                f"Mã có hiệu lực trong {minutes} phút và chỉ có thể sử dụng một lần. "
                "Vì lý do bảo mật, vui lòng không chia sẻ mã này với bất kỳ ai.\n\n"
                "Nếu bạn không thực hiện yêu cầu này, vui lòng bỏ qua email. "
                "Mật khẩu hiện tại của bạn vẫn được giữ nguyên.\n\n"
                "Trân trọng,\n"
                "Đội ngũ VidTrans\n"
                "Liên hệ hỗ trợ: 095510400\n"
            ),
        )
    except MailDeliveryError:
        logger.exception("Password reset e-mail delivery failed")


@app.post("/api/v1/auth/password-reset/request", status_code=202)
def request_password_reset(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(..., max_length=254),
) -> JSONResponse:
    """Send a one-time code; the response never reveals whether the e-mail exists."""
    require_password_reset_available()
    check_password_reset_rate_limit(auth_client_id(request))
    account = ADMIN_ACCOUNTS.get_by_email(email)
    if account is not None and account["approval_status"] == "approved":
        code = PASSWORD_RESETS.issue(str(account["user_id"]))
        if code is not None:
            # Delivery runs after the response so its latency cannot reveal the account.
            background_tasks.add_task(send_password_reset_email, str(account["email"]), code)
    return JSONResponse({"message": PASSWORD_RESET_GENERIC_MESSAGE}, status_code=202,
                        headers={"Cache-Control": "no-store"})


@app.post("/api/v1/auth/password-reset/confirm")
def confirm_password_reset(
    request: Request,
    email: str = Form(..., max_length=254),
    code: str = Form(..., max_length=16),
    new_password: str = Form(..., min_length=12, max_length=256),
) -> JSONResponse:
    """Set a new password with the e-mailed code and end every existing session."""
    require_password_reset_available()
    check_password_reset_rate_limit(auth_client_id(request))
    account = ADMIN_ACCOUNTS.get_by_email(email)
    if (
        account is None
        or account["approval_status"] != "approved"
        or not PASSWORD_RESETS.consume(str(account["user_id"]), code)
    ):
        raise HTTPException(status_code=400, detail=PASSWORD_RESET_INVALID_CODE)
    try:
        ADMIN_ACCOUNTS.set_password_hash(str(account["user_id"]), hash_password(new_password))
    except (AdminAccountError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    TENANT_GOVERNANCE.audit(
        actor_user_id=str(account["user_id"]),
        action="account.password_reset_self",
        resource_type="account",
        resource_id=str(account["user_id"]),
        target_user_id=str(account["user_id"]),
        metadata={"method": "email_code"},
    )
    response = JSONResponse(
        {"message": "Đã đặt lại mật khẩu. Hãy đăng nhập bằng mật khẩu mới."},
        headers={"Cache-Control": "no-store"},
    )
    clear_auth_cookies(response)
    return response


@app.delete("/api/v1/auth/session")
def delete_auth_session() -> JSONResponse:
    response = JSONResponse(
        {"authenticated": False, "message": "Đã đăng xuất VidTrans"},
        headers={"Cache-Control": "no-store"},
    )
    clear_auth_cookies(response)
    return response


@app.get("/api/v1/auth/proxy-check")
def check_proxy_auth() -> Response:
    """Lightweight protected endpoint used by Nginx auth_request."""

    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/auth/admin-proxy-check")
def check_admin_proxy_auth(request: Request) -> Response:
    """Authorize the shared source-login browser for administrators only."""

    require_roles(request, "owner", "admin")
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/douyin-browser/status")
def get_douyin_browser_status(request: Request, refresh: bool = Query(default=False)) -> JSONResponse:
    require_roles(request, "owner", "admin")
    return JSONResponse(DOUYIN_BROWSER_AUTH.status(refresh=refresh))


@app.post("/api/v1/douyin-browser/sync")
def sync_douyin_browser_session(request: Request) -> JSONResponse:
    require_roles(request, "owner", "admin")
    payload = DOUYIN_BROWSER_AUTH.sync()
    return JSONResponse(payload, status_code=200 if payload["available"] else 503)


@app.post("/api/v1/douyin-browser/reload")
def reload_douyin_browser(request: Request) -> JSONResponse:
    require_roles(request, "owner", "admin")
    try:
        return JSONResponse(DOUYIN_BROWSER_AUTH.reload())
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.delete("/api/v1/douyin-browser/session")
def delete_douyin_browser_session(request: Request) -> JSONResponse:
    require_roles(request, "owner", "admin")
    try:
        return JSONResponse(DOUYIN_BROWSER_AUTH.logout())
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/douyin-auth")
def get_douyin_auth_status(request: Request) -> JSONResponse:
    require_roles(request, "owner", "admin")
    return JSONResponse(DOUYIN_BROWSER_AUTH.status())

@app.delete("/api/v1/douyin-auth")
def logout_douyin(request: Request) -> JSONResponse:
    require_roles(request, "owner", "admin")
    DOUYIN_BROWSER_AUTH.logout()
    return JSONResponse({"authenticated": False, "message": "Đã xóa phiên đăng nhập Douyin"})


SOURCE_BROWSER_MANAGERS = {
    "douyin": DOUYIN_BROWSER_AUTH,
    "youtube": YOUTUBE_BROWSER_AUTH,
    "facebook": FACEBOOK_BROWSER_AUTH,
}


def _source_browser_manager(platform: str) -> Any:
    manager = SOURCE_BROWSER_MANAGERS.get(platform.lower())
    if manager is None:
        raise HTTPException(status_code=404, detail="Nền tảng trình duyệt không được hỗ trợ")
    return manager


@app.get("/api/v1/source-browser/{platform}/status")
def get_source_browser_status(request: Request, platform: str, refresh: bool = Query(default=False)) -> JSONResponse:
    require_roles(request, "owner", "admin")
    return JSONResponse(_source_browser_manager(platform).status(refresh=refresh))


@app.post("/api/v1/source-browser/{platform}/activate")
def activate_source_browser(request: Request, platform: str) -> JSONResponse:
    require_roles(request, "owner", "admin")
    try:
        return JSONResponse(_source_browser_manager(platform).reload())
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/source-browser/{platform}/sync")
def sync_source_browser(request: Request, platform: str) -> JSONResponse:
    require_roles(request, "owner", "admin")
    payload = _source_browser_manager(platform).sync()
    return JSONResponse(payload, status_code=200 if payload["available"] else 503)


@app.post("/api/v1/source-browser/{platform}/reload")
def reload_source_browser(request: Request, platform: str) -> JSONResponse:
    require_roles(request, "owner", "admin")
    try:
        return JSONResponse(_source_browser_manager(platform).reload())
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.delete("/api/v1/source-browser/{platform}/session")
def delete_source_browser_session(request: Request, platform: str) -> JSONResponse:
    require_roles(request, "owner", "admin")
    try:
        return JSONResponse(_source_browser_manager(platform).logout())
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/tiktok-auth")
def get_tiktok_auth_status(request: Request, account_id: str = Query(default="")) -> JSONResponse:
    publisher = tenant_publisher_for_request(request, "TIKTOK")
    status = publisher.connection_status(account_id or None)
    status["accounts"] = publisher.accounts()
    return JSONResponse(status)


@app.get("/api/v1/tiktok-auth/connect")
def connect_tiktok(request: Request) -> JSONResponse:
    try:
        authorization_url = tenant_publisher_for_request(request, "TIKTOK").authorization_url()
        register_tenant_oauth_session(request, "tiktok", authorization_url)
        return JSONResponse({"authorization_url": authorization_url})
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
        tenant_publisher_for_oauth_callback("tiktok", state).exchange_code(code, state)
        return RedirectResponse(url="/?tiktok=connected#tiktok", status_code=303)
    except (TikTokPublisherError, ValueError) as exc:
        message = quote_plus(str(exc))
        return RedirectResponse(url=f"/?tiktok_error={message}#tiktok", status_code=303)


@app.delete("/api/v1/tiktok-auth")
def disconnect_tiktok(request: Request, account_id: str = Query(default="")) -> JSONResponse:
    publisher = tenant_publisher_for_request(request, "TIKTOK")
    publisher.disconnect(account_id or None)
    status = publisher.connection_status()
    status["accounts"] = publisher.accounts()
    return JSONResponse(status)


@app.get("/api/v1/tts-providers")
def tts_providers_endpoint(request: Request) -> JSONResponse:
    providers = [dict(status.__dict__) for status in provider_statuses()]
    return JSONResponse({"providers": annotate_providers(current_account(request), "tts", providers)})


@app.get("/api/v1/tts-providers/elevenlabs/voices/{voice_key}/preview")
def elevenlabs_voice_preview_endpoint(voice_key: str) -> Response:
    try:
        audio, content_type = elevenlabs_voice_preview(voice_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.warning("ElevenLabs voice preview failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(audio, media_type=content_type, headers={"Cache-Control": "private, max-age=86400"})


_edge_voice_previews: dict[str, bytes] = {}


def edge_voice_preview(voice_key: str) -> tuple[bytes, str]:
    voice_name = VOICE_OPTIONS.get(voice_key)
    if voice_name is None:
        raise LookupError("Giọng Edge TTS không tồn tại")
    if voice_key not in _edge_voice_previews:
        with tempfile.TemporaryDirectory(prefix="vidtrans-preview-") as directory:
            output_path = Path(directory) / "preview.mp3"
            try:
                tts_edge_with_retry(VOICE_PREVIEW_TEXT, output_path, voice_name, "+0%")
            except Exception as exc:
                raise RuntimeError("Edge TTS không tạo được bản nghe thử") from exc
            _edge_voice_previews[voice_key] = output_path.read_bytes()
    audio = _edge_voice_previews[voice_key]
    return audio, audio_content_type(audio)


@app.get("/api/v1/tts-providers/{provider}/voices/{voice_key}/preview")
def tts_voice_preview_endpoint(provider: str, voice_key: str) -> Response:
    previewers = {"cit": cit_voice_preview, "edge": edge_voice_preview}
    if provider not in previewers:
        raise HTTPException(status_code=404, detail="Nhà cung cấp này chưa hỗ trợ nghe thử")
    try:
        audio, content_type = previewers[provider](voice_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.warning("%s voice preview failed: %s", provider, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(audio, media_type=content_type, headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/v1/translation-providers")
def translation_providers_endpoint(request: Request) -> JSONResponse:
    providers = [dict(status.__dict__) for status in translation_provider_statuses()]
    return JSONResponse({"providers": annotate_providers(current_account(request), "translation", providers)})


@app.post("/process-video", deprecated=True)
@app.post("/convert", deprecated=True)
async def process_video_endpoint(
    http_request: Request,
    file: UploadFile = File(...),
    mode: int = Form(...),
    voice_type: str = Form("female"),
    voice_mode: str = Form("auto"),
    voice_style: str = Form("natural"),
    # Empty means the server default: CIT Voice Studio / TranslateGemma when running.
    tts_provider: str = Form(""),
    elevenlabs_female_voice: str = Form(""),
    elevenlabs_male_voice: str = Form(""),
    tts_female_voice: str = Form(""),
    tts_male_voice: str = Form(""),
    translation_provider: str = Form(""),
    speech_rate: float = Form(1.0),
    background_music: UploadFile | None = File(default=None),
    whisper_model: str = Form("base"),
    keep_original_audio: bool = Form(True),
    original_audio_volume: float = Form(DEFAULT_ORIGINAL_AUDIO_VOLUME),
    music_volume: float = Form(0.28),
    subtitle_style: str | None = Form(default=None),
    subtitle_source: str = Form("speech"),
    source_language: str = Form("zh-CN"),
    target_language: str = Form("vi"),
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
    account = current_account(http_request)
    owner_user_id = str(account["user_id"])
    tts_provider = tts_provider.strip().lower() or default_tts_provider()
    translation_provider = translation_provider.strip().lower() or default_translation_provider()
    try:
        ensure_video_providers_allowed(
            account, translation_provider=translation_provider, tts_provider=tts_provider, dubbing=mode != 1
        )
    except PlanEntitlementError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
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
            voice_style=voice_style,
            tts_provider=tts_provider,
            translation_provider=translation_provider,
            source_language=source_language,
            target_language=target_language,
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
    tts_voices: dict[str, str] = {}
    if request.mode is not ProcessingMode.SUBTITLES:
        try:
            ensure_provider_configured(request.tts_provider)
            tts_voices = resolve_tts_voice_selection(
                request.tts_provider,
                tts_female_voice or elevenlabs_female_voice,
                tts_male_voice or elevenlabs_male_voice,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        ensure_translation_provider_configured(request.translation_provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = uuid.uuid4().hex[:8]
    validate_tiktok_auto_publish(request, owner_user_id)
    reserve_job_quota(job_id, account)
    upload_dir = tenant_job_dir(UPLOAD_DIR, owner_user_id, job_id)
    video_path = upload_dir / f"source{video_ext}"
    with video_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    try:
        validate_video_file(video_path, ffprobe=FFPROBE)
    except InvalidVideoError as exc:
        video_path.unlink(missing_ok=True)
        TENANT_GOVERNANCE.release(job_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    background_music_path = None
    if background_music is not None:
        music_ext = Path(background_music.filename or "music.mp3").suffix or ".mp3"
        background_music_path = upload_dir / f"background{music_ext}"
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
            TENANT_GOVERNANCE.release(job_id)
            video_path.unlink(missing_ok=True)
            if background_music_path is not None:
                background_music_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    resume_request = {
        "video_path": str(video_path),
        "mode": int(request.mode),
        "whisper_model": whisper_model,
        "voice_type": request.fallback_voice.value,
        "voice_mode": request.voice_mode.value,
        "voice_style": request.voice_style,
        "tts_provider": request.tts_provider,
        "tts_voices": tts_voices,
        "translation_provider": request.translation_provider,
        "speech_rate": speech_rate,
        "background_music_path": str(background_music_path) if background_music_path else None,
        "keep_original_audio": keep_original_audio,
        "original_audio_volume": original_audio_volume,
        "music_volume": music_volume,
        "subtitle_style": style_payload,
        "subtitle_source": request.subtitle_source.value,
        "source_language": request.source_language,
        "target_language": request.target_language,
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

    created_job = JOB_SERVICE.create(job_id, {
        "status": "queued",
        "step": "queued",
        "progress": 0.0,
        "mode": int(request.mode),
        "filename": file.filename,
        "subtitle_source": request.subtitle_source.value,
        "source_language": request.source_language,
        "target_language": request.target_language,
        "voice_routing": {
            "mode": request.voice_mode.value,
            "fallback_voice": request.fallback_voice.value,
            "voice_style": request.voice_style,
            "tts_provider": request.tts_provider,
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
    }, owner_user_id=owner_user_id)
    emit_workflow_event("source.uploaded", job_id, created_job)
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
    request: Request,
    files: list[UploadFile] | None = File(default=None),
    source_links: str = Form(""),
    source_cookies: UploadFile | None = File(default=None),
    batch_name: str = Form("Batch mới"),
    mode: int = Form(1),
    voice_type: str = Form("female"),
    voice_mode: str = Form("auto"),
    voice_style: str = Form("natural"),
    # Empty means the server default: CIT Voice Studio / TranslateGemma when running.
    tts_provider: str = Form(""),
    elevenlabs_female_voice: str = Form(""),
    elevenlabs_male_voice: str = Form(""),
    tts_female_voice: str = Form(""),
    tts_male_voice: str = Form(""),
    translation_provider: str = Form(""),
    speech_rate: float = Form(1.0),
    background_music: UploadFile | None = File(default=None),
    whisper_model: str = Form("base"),
    keep_original_audio: bool = Form(True),
    original_audio_volume: float = Form(DEFAULT_ORIGINAL_AUDIO_VOLUME),
    music_volume: float = Form(0.28),
    subtitle_style: str | None = Form(default=None),
    subtitle_source: str = Form("speech"),
    source_language: str = Form("zh-CN"),
    target_language: str = Form("vi"),
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
    account = current_account(request)
    owner_user_id = str(account["user_id"])
    tts_provider = tts_provider.strip().lower() or default_tts_provider()
    translation_provider = translation_provider.strip().lower() or default_translation_provider()
    try:
        ensure_video_providers_allowed(
            account, translation_provider=translation_provider, tts_provider=tts_provider, dubbing=mode != 1
        )
    except PlanEntitlementError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    # Browsers serialize an untouched <input type="file"> as an empty UploadFile.
    # Ignore only that unnamed placeholder; named zero-byte uploads are still
    # validated below and reported to the user as invalid videos.
    uploads = [upload for upload in (files or []) if (upload.filename or "").strip()]
    try:
        source_urls = extract_social_video_urls(source_links, limit=50)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if source_links.strip() and not source_urls:
        raise HTTPException(
            status_code=400,
            detail="Không tìm thấy link TikTok, Douyin, YouTube hoặc Facebook hợp lệ",
        )
    cookie_payload: bytes | None = None
    if source_cookies is not None and source_cookies.filename:
        require_roles(request, "owner", "admin")
        if not source_urls:
            raise HTTPException(status_code=400, detail="Chỉ dùng cookies.txt khi batch có link mạng xã hội")
        cookie_payload = await source_cookies.read(512 * 1024 + 1)
        if len(cookie_payload) > 512 * 1024:
            raise HTTPException(status_code=400, detail="File cookies.txt không được vượt quá 512 KB")
        if not cookie_payload or b"\x00" in cookie_payload:
            raise HTTPException(status_code=400, detail="File cookies.txt không hợp lệ")
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
        processing_request = ProcessingRequest.from_form(
            mode=mode,
            subtitle_source=subtitle_source,
            ocr_sample_fps=ocr_sample_fps,
            ocr_roi_top=ocr_roi_top,
            ocr_roi_bottom=ocr_roi_bottom,
            voice_mode=voice_mode,
            voice_type=voice_type,
            voice_style=voice_style,
            tts_provider=tts_provider,
            translation_provider=translation_provider,
            source_language=source_language,
            target_language=target_language,
            generate_tiktok_post=generate_tiktok_post,
            tiktok_max_summary_chars=tiktok_max_summary_chars,
            tiktok_hashtag_count=tiktok_hashtag_count,
            auto_publish_tiktok=auto_publish_tiktok,
            tiktok_privacy_level=tiktok_privacy_level,
            tiktok_publish_at=tiktok_publish_at,
        )
        OCRConfig(
            sample_fps=processing_request.ocr.sample_fps,
            roi_top=processing_request.ocr.roi_top,
            roi_bottom=processing_request.ocr.roi_bottom,
        ).validate()
        style_payload = json.loads(subtitle_style) if subtitle_style else None
        if style_payload is not None and not isinstance(style_payload, dict):
            raise ValueError("subtitle_style must be a JSON object")
        SubtitleLayoutOptions.from_style(style_payload or {})
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if processing_request.mode is ProcessingMode.DUBBED_WITH_MUSIC and background_music is None:
        raise HTTPException(status_code=400, detail="background_music is required for mode 3")
    tts_voices: dict[str, str] = {}
    if processing_request.mode is not ProcessingMode.SUBTITLES:
        try:
            ensure_provider_configured(processing_request.tts_provider)
            tts_voices = resolve_tts_voice_selection(
                processing_request.tts_provider,
                tts_female_voice or elevenlabs_female_voice,
                tts_male_voice or elevenlabs_male_voice,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        ensure_translation_provider_configured(processing_request.translation_provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    validate_tiktok_auto_publish(processing_request, owner_user_id)

    limit = account_plan_limit(account)
    if limit is not None:
        overview = TENANT_GOVERNANCE.overview(owner_user_id, limit)
        if overview["remaining_minutes"] is not None and overview["remaining_minutes"] < (len(uploads) + len(source_urls)):
            raise HTTPException(
                status_code=402,
                detail=f"Đã hết quota gói {str(account.get('plan', 'free')).upper()}: Gói còn {overview['remaining_minutes']} phút, không đủ giữ chỗ cho {len(uploads) + len(source_urls)} video mới.",
            )

    batch_id = f"batch_{uuid.uuid4().hex[:10]}"
    upload_job_ids = [uuid.uuid4().hex[:8] for _ in uploads]
    source_job_ids = [uuid.uuid4().hex[:8] for _ in source_urls]
    reserve_batch_quota([*upload_job_ids, *source_job_ids], account)
    normalized_name = " ".join(batch_name.split())[:120] or f"Batch {batch_id[-6:]}"
    background_music_path: Path | None = None
    if background_music is not None:
        music_ext = Path(background_music.filename or "music.mp3").suffix or ".mp3"
        background_music_path = tenant_job_dir(UPLOAD_DIR, owner_user_id, batch_id) / f"background{music_ext}"
        with background_music_path.open("wb") as buffer:
            shutil.copyfileobj(background_music.file, buffer)

    batch_config = {
        "mode": int(processing_request.mode),
        "subtitle_source": processing_request.subtitle_source.value,
        "source_language": processing_request.source_language,
        "target_language": processing_request.target_language,
        "voice_mode": processing_request.voice_mode.value,
        "voice_type": processing_request.fallback_voice.value,
        "voice_style": processing_request.voice_style,
        "tts_provider": processing_request.tts_provider,
        "tts_voices": tts_voices,
        "translation_provider": processing_request.translation_provider,
        "speech_rate": speech_rate,
        "whisper_model": whisper_model,
        "keep_original_audio": keep_original_audio,
        "original_audio_volume": original_audio_volume,
        "music_volume": music_volume,
        "subtitle_style": style_payload,
        "ocr_sample_fps": processing_request.ocr.sample_fps,
        "ocr_roi_top": processing_request.ocr.roi_top,
        "ocr_roi_bottom": processing_request.ocr.roi_bottom,
        "generate_tiktok_post": processing_request.tiktok.enabled,
        "tiktok_max_summary_chars": processing_request.tiktok.max_summary_chars,
        "tiktok_hashtag_count": processing_request.tiktok.hashtag_count,
        "auto_publish_tiktok": processing_request.tiktok.auto_publish,
        "tiktok_privacy_level": processing_request.tiktok.privacy_level,
        "tiktok_publish_at": processing_request.tiktok.publish_at,
        "background_music_path": str(background_music_path) if background_music_path else None,
    }
    prepared_uploads: list[tuple[str, UploadFile, Path]] = []
    try:
        for upload_job_id, upload in zip(upload_job_ids, uploads):
            video_ext = Path(upload.filename or "video.mp4").suffix.lower() or ".mp4"
            upload_video_path = tenant_job_dir(UPLOAD_DIR, owner_user_id, upload_job_id) / f"source{video_ext}"
            with upload_video_path.open("wb") as buffer:
                shutil.copyfileobj(upload.file, buffer)
            validate_video_file(upload_video_path, ffprobe=FFPROBE)
            prepared_uploads.append((upload_job_id, upload, upload_video_path))
    except InvalidVideoError as exc:
        for reserved_job_id in [*upload_job_ids, *source_job_ids]:
            TENANT_GOVERNANCE.release(reserved_job_id)
        for _, _, prepared_path in prepared_uploads:
            prepared_path.unlink(missing_ok=True)
        # The failing file is not appended to prepared_uploads yet.
        if "upload_video_path" in locals():
            upload_video_path.unlink(missing_ok=True)
        if background_music_path is not None:
            background_music_path.unlink(missing_ok=True)
        invalid_name = upload.filename or "video"
        raise HTTPException(status_code=400, detail=f"{invalid_name}: {exc}") from exc

    JOB_SERVICE.create_batch(batch_id, name=normalized_name, config=batch_config, owner_user_id=owner_user_id)

    created_jobs: list[dict[str, Any]] = []
    try:
        for job_id, upload, video_path in prepared_uploads:
            resume_request = {**batch_config, "video_path": str(video_path)}
            created_job = JOB_SERVICE.create(
                job_id,
                {
                    "status": "queued",
                    "step": "queued",
                    "step_detail": "Video đã tải lên máy chủ, đang chờ xử lý",
                    "progress": 0.0,
                    "mode": int(processing_request.mode),
                    "filename": upload.filename,
                    "subtitle_source": processing_request.subtitle_source.value,
                    "source_language": processing_request.source_language,
                    "target_language": processing_request.target_language,
                    "voice_routing": {
                        "mode": processing_request.voice_mode.value,
                        "fallback_voice": processing_request.fallback_voice.value,
                        "voice_style": processing_request.voice_style,
                    },
                    "tiktok": {
                        "enabled": processing_request.tiktok.enabled,
                        "max_summary_chars": processing_request.tiktok.max_summary_chars,
                        "hashtag_count": processing_request.tiktok.hashtag_count,
                        "auto_publish": processing_request.tiktok.auto_publish,
                        "privacy_level": processing_request.tiktok.privacy_level,
                        "publish_at": processing_request.tiktok.publish_at,
                    },
                    "resume_request": resume_request,
                    "ocr_config": {
                        "sample_fps": processing_request.ocr.sample_fps,
                        "roi_top": processing_request.ocr.roi_top,
                        "roi_bottom": processing_request.ocr.roi_bottom,
                    },
                },
                owner_user_id=owner_user_id,
            )
            JOB_SERVICE.attach_to_batch(job_id, batch_id)
            created_job = {**created_job, "batch_id": batch_id}
            emit_workflow_event("source.uploaded", job_id, created_job)
            created_jobs.append({"job_id": job_id, "filename": upload.filename, "status": "queued"})

        for job_id, source_url in zip(source_job_ids, source_urls):
            platform = social_platform(source_url)
            initial_filename = f"Video {platform} · {job_id}"
            source_cookie_path: Path | None = None
            source_upload_dir = tenant_job_dir(UPLOAD_DIR, owner_user_id, job_id)
            if cookie_payload is not None:
                source_cookie_path = source_upload_dir / "source.cookies.txt"
                with source_cookie_path.open("wb") as cookie_buffer:
                    cookie_buffer.write(cookie_payload)
                try:
                    source_cookie_path.chmod(0o600)
                except OSError:
                    logger.warning("Unable to restrict cookie file permissions for job %s", job_id)
            resume_request = {
                **batch_config,
                "video_path": str(source_upload_dir / "source.pending"),
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
                    "mode": int(processing_request.mode),
                    "filename": initial_filename,
                    "source_url": source_url,
                    "source_platform": platform,
                    "subtitle_source": processing_request.subtitle_source.value,
                    "source_language": processing_request.source_language,
                    "target_language": processing_request.target_language,
                    "voice_routing": {
                        "mode": processing_request.voice_mode.value,
                        "fallback_voice": processing_request.fallback_voice.value,
                        "voice_style": processing_request.voice_style,
                    },
                    "tiktok": {
                        "enabled": processing_request.tiktok.enabled,
                        "max_summary_chars": processing_request.tiktok.max_summary_chars,
                        "hashtag_count": processing_request.tiktok.hashtag_count,
                        "auto_publish": processing_request.tiktok.auto_publish,
                        "privacy_level": processing_request.tiktok.privacy_level,
                        "publish_at": processing_request.tiktok.publish_at,
                    },
                    "resume_request": resume_request,
                    "ocr_config": {
                        "sample_fps": processing_request.ocr.sample_fps,
                        "roi_top": processing_request.ocr.roi_top,
                        "roi_bottom": processing_request.ocr.roi_bottom,
                    },
                },
                owner_user_id=owner_user_id,
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
    except Exception:
        for reserved_job_id in [*upload_job_ids, *source_job_ids]:
            TENANT_GOVERNANCE.release(reserved_job_id)
        raise

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
        **{key: value for key, value in job.items() if key not in {"resume_request", "owner_user_id"}},
    }
    if response.get("error"):
        response["error"] = public_error_message(str(response["error"]))
    if queue_positions and job_id in queue_positions:
        response["queue_position"] = queue_positions[job_id]
    if job.get("output_video"):
        response["video_url"] = f"/api/v1/jobs/{job_id}/artifacts/video"
    if job.get("subtitle_file"):
        response["subtitle_url"] = f"/api/v1/jobs/{job_id}/artifacts/subtitle"
    if job.get("translation_file"):
        response["translation_url"] = f"/api/v1/jobs/{job_id}/artifacts/translation"
    if job.get("tiktok_json_file"):
        response["tiktok_json_url"] = f"/api/v1/jobs/{job_id}/artifacts/tiktok-json"
    if job.get("tiktok_text_file"):
        response["tiktok_text_url"] = f"/api/v1/jobs/{job_id}/artifacts/tiktok-text"
    if any(job.get(key) for key in ("output_video", "subtitle_file", "translation_file")):
        response["download_all_url"] = f"/api/v1/jobs/{job_id}/download-all"
    return response


@app.get("/api/v1/jobs")
def list_jobs_endpoint(
    request: Request,
    status: str | None = Query(default=None),
    batch_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    job_action: str | None = Query(default=None),
    page: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    account = current_account(request)
    owner_scope = None if can_manage_all_jobs(account) else str(account["user_id"])
    resolved_offset = (page - 1) * limit if page is not None else offset
    jobs, total = JOB_SERVICE.list_jobs(
        owner_user_id=owner_scope,
        status=status,
        batch_id=batch_id,
        search=search,
        job_action=job_action,
        limit=limit,
        offset=resolved_offset,
    )
    total_pages = max(1, (total + limit - 1) // limit)
    if page is not None and page > total_pages and total > 0:
        page = total_pages
        resolved_offset = (page - 1) * limit
        jobs, total = JOB_SERVICE.list_jobs(
            owner_user_id=owner_scope,
            status=status,
            batch_id=batch_id,
            search=search,
            job_action=job_action,
            limit=limit,
            offset=resolved_offset,
        )
    resolved_page = page if page is not None else (resolved_offset // limit) + 1
    positions = JOB_SERVICE.queue_positions()
    counts = JOB_SERVICE.status_counts(owner_user_id=owner_scope)
    items = [
        add_job_owner_identity(job_response(str(job["job_id"]), job, positions), job)
        for job in jobs
    ]
    return JSONResponse({
        "items": items,
        "total": total,
        "limit": limit,
        "offset": resolved_offset,
        "page": resolved_page,
        "total_pages": total_pages,
        "counts": counts,
    })


JOB_EVENT_RESYNC = {"type": "jobs", "resync": True, "items": [], "removed": []}


def job_change_snapshot(job_ids: set[str]) -> dict[str, Any]:
    """Serialize changed jobs exactly like GET /api/v1/jobs does."""

    if ALL_JOBS in job_ids or len(job_ids) > 200:
        return dict(JOB_EVENT_RESYNC)
    positions = JOB_SERVICE.queue_positions()
    items: list[dict[str, Any]] = []
    removed: list[str] = []
    for job_id in sorted(job_ids):
        job = JOB_SERVICE.get(job_id)
        if job is None:
            removed.append(job_id)
        else:
            item = add_job_owner_identity(job_response(job_id, job, positions), job)
            item["_owner_user_id"] = str(job.get("owner_user_id") or "")
            items.append(item)
    return {"type": "jobs", "items": items, "removed": removed, "queue_positions": positions}


def filter_job_event_for_user(message: dict[str, Any], owner_user_id: str, *, allow_all: bool = False) -> dict[str, Any] | None:
    if message.get("resync"):
        return dict(JOB_EVENT_RESYNC)
    items = [
        dict(item) for item in message.get("items", [])
        if allow_all or item.get("_owner_user_id") == owner_user_id
    ]
    for item in items:
        item.pop("_owner_user_id", None)
    # Deletions cannot be attributed after the row is gone; a scoped reload is safe.
    if message.get("removed"):
        return dict(JOB_EVENT_RESYNC)
    if not items:
        return None
    positions = message.get("queue_positions") or {}
    return {
        "type": "jobs",
        "items": items,
        "removed": [],
        "queue_positions": {item["job_id"]: positions[item["job_id"]] for item in items if item["job_id"] in positions},
    }


def websocket_auth_claims(websocket: WebSocket) -> dict[str, Any] | None:
    """Apply the HTTP middleware's auth rules to a WebSocket handshake.

    Native apps send ``Authorization: Bearer``; nothing ambient rides along, so the
    Origin is not checked. Browsers cannot set that header on a WebSocket and use the
    session cookie instead; because cookies ride along on cross-site handshakes, the
    Origin must then match the Host the page was served from.
    """

    authorization = websocket.headers.get("authorization", "").strip()
    scheme, _, bearer = authorization.partition(" ")
    if authorization and (scheme.lower() != "bearer" or not bearer.strip()):
        return None
    if not authorization:
        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host", "").rsplit(":", 1)[0].strip("[]").lower()
        if origin and (urlsplit(origin).hostname or "").lower() != host:
            return None
    if not AUTH_MANAGER.config.enabled:
        return {}
    if not AUTH_MANAGER.config.configured:
        return None
    token = bearer.strip() or websocket.cookies.get(AUTH_MANAGER.config.cookie_name)
    if not token:
        return None
    try:
        claims = AUTH_MANAGER.verify_token(token)
        account = ADMIN_ACCOUNTS.get(str(claims["user_id"]))
    except (AuthConfigurationError, InvalidTokenError, AdminAccountError, KeyError):
        return None
    if account["approval_status"] != "approved":
        return None
    return claims


@app.websocket("/api/v1/jobs/events")
async def job_events_socket(websocket: WebSocket) -> None:
    """Push job progress instead of having every tab poll the full job list."""

    claims = await asyncio.to_thread(websocket_auth_claims, websocket)
    if claims is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    expires_at = float(claims.get("exp") or 0)
    owner_user_id = str(claims.get("user_id") or _bootstrap_owner["user_id"])
    queue = JOB_EVENTS.subscribe()

    async def read_until_closed() -> None:
        while True:
            await websocket.receive_text()

    reader = asyncio.create_task(read_until_closed())
    try:
        await websocket.send_json({"type": "ready"})
        while True:
            getter = asyncio.create_task(queue.get())
            done, _pending = await asyncio.wait({getter, reader}, timeout=25, return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                getter.cancel()
                break
            if expires_at and time.time() >= expires_at:
                getter.cancel()
                await websocket.close(code=4401)
                break
            if getter in done:
                scoped = filter_job_event_for_user(
                    getter.result(), owner_user_id,
                    allow_all=str(claims.get("role") or "") in {"owner", "admin"},
                )
                if scoped is not None:
                    await websocket.send_json(scoped)
            else:
                # Keep proxies from closing an idle connection.
                getter.cancel()
                await websocket.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        JOB_EVENTS.unsubscribe(queue)
        reader.cancel()


@app.websocket("/api/v1/admin/system/events")
async def admin_system_events_socket(websocket: WebSocket) -> None:
    """Push server metrics and the process table to the admin monitor page."""

    claims = await asyncio.to_thread(websocket_auth_claims, websocket)
    # Like the HTTP routes, refuse the auth-disabled fallback: host metrics
    # describe the whole server.
    account = None
    if claims and claims.get("user_id"):
        try:
            account = await asyncio.to_thread(ADMIN_ACCOUNTS.get, str(claims["user_id"]))
        except AdminAccountError:
            account = None
    if account is None or account.get("role") not in {"owner", "admin"}:
        await websocket.close(code=4403 if account is not None else 4401)
        return
    await websocket.accept()
    await audit_process_view(str(account["user_id"]))
    expires_at = float(claims.get("exp") or 0)
    queue = SYSTEM_EVENTS.subscribe()

    async def read_until_closed() -> None:
        while True:
            await websocket.receive_text()

    reader = asyncio.create_task(read_until_closed())
    try:
        await websocket.send_json({"type": "ready"})
        while True:
            getter = asyncio.create_task(queue.get())
            done, _pending = await asyncio.wait({getter, reader}, timeout=25, return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                getter.cancel()
                break
            if expires_at and time.time() >= expires_at:
                getter.cancel()
                await websocket.close(code=4401)
                break
            if getter in done:
                await websocket.send_json(getter.result())
            else:
                getter.cancel()
                await websocket.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        SYSTEM_EVENTS.unsubscribe(queue)
        reader.cancel()


@app.get("/api/v1/jobs/{job_id}")
def get_job_endpoint(request: Request, job_id: str) -> JSONResponse:
    job = job_for_request(request, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(add_job_owner_identity(job_response(job_id, job, JOB_SERVICE.queue_positions()), job))


@app.post("/api/v1/jobs/{job_id}/refresh-tiktok-status")
def refresh_tiktok_publish_status(request: Request, job_id: str) -> JSONResponse:
    job = job_for_request(request, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    owner_user_id = str(job.get("owner_user_id") or current_account(request)["user_id"])
    publish_id = str(job.get("tiktok_publish_id") or "")
    if not publish_id:
        raise HTTPException(status_code=409, detail="Job chưa gửi video lên TikTok")
    try:
        detail = tenant_tiktok_publisher(owner_user_id).fetch_status(publish_id)
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
def download_all_job_artifacts(request: Request, job_id: str) -> FileResponse:
    job = job_for_request(request, job_id)
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
        target = stored_artifact_path(str(filename))
        if target.is_file():
            artifacts.append(target)
    if not artifacts:
        raise HTTPException(status_code=404, detail="job chưa có artifact để tải")
    archive_path = build_artifact_archive(artifacts, WORK_DIR)
    return FileResponse(
        archive_path,
        filename=f"{job_id}.artifacts.zip",
        media_type="application/zip",
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


@app.get("/api/v1/batches")
def list_batches_endpoint(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    account = current_account(request)
    batches, total = JOB_SERVICE.list_batches(
        owner_user_id=None if can_manage_all_jobs(account) else str(account["user_id"]), limit=limit, offset=offset
    )
    for batch in batches:
        batch.pop("owner_user_id", None)
        if isinstance(batch.get("config"), dict):
            batch["config"].pop("background_music_path", None)
    return JSONResponse({"items": batches, "total": total, "limit": limit, "offset": offset})


@app.get("/api/v1/batches/{batch_id}")
def get_batch_endpoint(request: Request, batch_id: str) -> JSONResponse:
    account = current_account(request)
    owner_user_id = str(account["user_id"])
    batch = JOB_SERVICE.get_batch(batch_id) if can_manage_all_jobs(account) else JOB_SERVICE.get_batch_for_user(batch_id, owner_user_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    batch.pop("owner_user_id", None)
    if isinstance(batch.get("config"), dict):
        batch["config"].pop("background_music_path", None)
    jobs, total = JOB_SERVICE.list_jobs(
        owner_user_id=None if can_manage_all_jobs(account) else owner_user_id, batch_id=batch_id, limit=200
    )
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
def cancel_job_endpoint(request: Request, job_id: str) -> JSONResponse:
    if job_for_request(request, job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    job = JOB_SERVICE.request_cancel(job_id)
    if job.get("status") == "cancelled":
        TENANT_GOVERNANCE.release(job_id)
    return JSONResponse(job_response(job_id, job))


@app.post("/api/v1/jobs/{job_id}/retry")
def retry_job_endpoint(request: Request, job_id: str) -> JSONResponse:
    account = current_account(request)
    source = job_for_request(request, job_id)
    if source is None:
        raise HTTPException(status_code=404, detail="job not found")
    owner_user_id = str(source.get("owner_user_id") or account["user_id"])
    if source.get("status") in {"queued", "scheduled", "processing", "cancelling"}:
        raise HTTPException(status_code=409, detail="Không thể chạy lại job đang xử lý")
    # A job keeps the providers it was created with; a later downgrade must not keep paid AI running.
    entitlement_account = ADMIN_ACCOUNTS.get(owner_user_id) if can_manage_all_jobs(account) else account
    if is_comic_job(source):
        previous = source.get("resume_request") or {}
        try:
            ensure_comic_settings_allowed(entitlement_account, previous.get("settings") or {})
        except PlanEntitlementError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        try:
            requeue_comic_job(COMIC_ROUTE_DEPS, request, job_id, source, render=bool(previous.get("render", True)),
                              preview_shot=previous.get("preview_shot"))
        except JobStateConflictError:
            raise HTTPException(status_code=409, detail="Không thể chạy lại job đang xử lý") from None
        return JSONResponse(job_response(job_id, JOB_SERVICE.get(job_id)), status_code=200)

    resume_request = source.get("resume_request")
    if not isinstance(resume_request, dict):
        raise HTTPException(status_code=409, detail="job không có cấu hình retry")
    video_path = Path(str(resume_request.get("video_path", "")))
    source_url = resume_request.get("source_url")
    if not video_path.is_file() and not source_url:
        raise HTTPException(status_code=409, detail="file video nguồn không còn tồn tại")
    try:
        ensure_video_providers_allowed(
            entitlement_account,
            translation_provider=str(resume_request.get("translation_provider") or "google"),
            tts_provider=str(resume_request.get("tts_provider") or "edge"),
            dubbing=int(resume_request.get("mode") or 1) != 1,
        )
    except PlanEntitlementError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    quota_account = ADMIN_ACCOUNTS.get(owner_user_id) if can_manage_all_jobs(account) else account
    reserve_job_quota(job_id, quota_account, minutes=1)

    update_payload = {
        "status": "queued",
        "step": "queued",
        "progress": 0.0,
        "error": None,
        "error_message": None,
        "step_detail": None,
        "cancel_requested": 0,
        "worker_id": None,
        "started_at": None,
        "finished_at": None,
        "completed_at": None,
        "tiktok_publish_id": None,
        "tiktok_publish_status": None,
        "tiktok_publish_detail": None,
        "tts_fallback": None,
        "translation_backup_cues": None,
    }
    artifact_keys = ("output_video", "subtitle_file", "translation_file", "tiktok_json_file", "tiktok_text_file")
    for key in artifact_keys:
        update_payload[key] = None

    # The status check above is a fast path; the store re-checks it atomically
    # so a job whose worker has not stopped can never be requeued twice.
    try:
        JOB_SERVICE.retry(job_id, **update_payload)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="job not found") from None
    except JobStateConflictError:
        raise HTTPException(status_code=409, detail="Không thể chạy lại job đang xử lý") from None

    for key in artifact_keys:
        filename = source.get(key)
        if filename:
            target = stored_artifact_path(str(filename))
            if target.is_relative_to(OUTPUT_DIR.resolve()):
                target.unlink(missing_ok=True)
    # A finished job is retried to get a fresh result: drop its stage checkpoints
    # so every step after the download runs again. Failed/cancelled jobs keep
    # them and resume from the step that stopped.
    if source.get("status") == "completed":
        work_dir = tenant_job_dir(WORK_DIR, owner_user_id, job_id)
        if not video_path.resolve().is_relative_to(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
    notify_scheduler()
    return JSONResponse(job_response(job_id, JOB_SERVICE.get(job_id)), status_code=200)


@app.delete("/api/v1/jobs/{job_id}")
def delete_job_endpoint(request: Request, job_id: str) -> JSONResponse:
    job = job_for_request(request, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    active = TIKTOK_BROWSER.active_attempt()
    if active and active["job_id"] == job_id:
        raise HTTPException(status_code=409, detail="Hãy kết thúc lượt chuẩn bị TikTok trước khi xóa video")
    if job.get("status") in {"queued", "processing", "scheduled", "cancelling"}:
        raise HTTPException(status_code=409, detail="Hãy hủy và chờ job dừng trước khi xóa")
    # Soft delete: the job disappears from the user's views, but its row and
    # files are kept until the retention policy purges them. The store
    # atomically re-checks the terminal status.
    try:
        JOB_SERVICE.delete(job_id, require_terminal=True)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="job not found") from None
    except JobStateConflictError:
        raise HTTPException(status_code=409, detail="Hãy hủy và chờ job dừng trước khi xóa") from None
    TENANT_GOVERNANCE.release(job_id)
    return JSONResponse({"job_id": job_id, "deleted": True})


@app.get("/status/{job_id}", deprecated=True)
def get_status(request: Request, job_id: str) -> JSONResponse:
    job = job_for_request(request, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(job_response(job_id, job, JOB_SERVICE.queue_positions()))


@app.get("/download/{filename}", deprecated=True)
def download(request: Request, filename: str) -> FileResponse:
    """Compatibility download with mandatory ownership verification."""
    owner_user_id = str(current_account(request)["user_id"])
    safe_name = Path(filename).name
    account = current_account(request)
    owns_artifact = JOB_SERVICE.owns_artifact(owner_user_id, safe_name)
    if can_manage_all_jobs(account) and not owns_artifact:
        matches, _ = JOB_SERVICE.list_jobs(search=safe_name, limit=200)
        owns_artifact = any(
            safe_name in {
                str(job.get(field) or ""), Path(str(job.get(field) or "")).name
            }
            for job in matches
            for field in ("output_video", "subtitle_file", "translation_file", "tiktok_json_file", "tiktok_text_file")
        )
    if not owns_artifact:
        raise HTTPException(status_code=404, detail="file not found")
    target = stored_artifact_path(safe_name)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target, headers={"Cache-Control": "private, no-store"})


@app.get("/api/v1/jobs/{job_id}/artifacts/{kind}")
def download_job_artifact(request: Request, job_id: str, kind: str) -> FileResponse:
    job = job_for_request(request, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="file not found")
    artifact_fields = {
        "video": "output_video",
        "subtitle": "subtitle_file",
        "translation": "translation_file",
        "tiktok-json": "tiktok_json_file",
        "tiktok-text": "tiktok_text_file",
    }
    field = artifact_fields.get(kind)
    filename = str(job.get(field) or "") if field else ""
    target = stored_artifact_path(filename) if filename else OUTPUT_DIR.resolve()
    if not filename or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target, filename=target.name, headers={"Cache-Control": "private, no-store"})


def comic_quota_account(request: Request, job: dict[str, Any]) -> dict[str, Any]:
    account = current_account(request)
    owner_user_id = str(job.get("owner_user_id") or account["user_id"])
    return ADMIN_ACCOUNTS.get(owner_user_id) if can_manage_all_jobs(account) else account


def list_comic_projects_for_request(
    request: Request,
    *,
    limit: int = 6,
    offset: int = 0,
    search: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    account = current_account(request)
    owner_scope = None if can_manage_all_jobs(account) else str(account["user_id"])
    jobs, total = JOB_SERVICE.list_jobs(
        owner_user_id=owner_scope,
        job_action=COMIC_JOB_ACTION,
        search=search,
        limit=limit,
        offset=offset,
    )
    positions = JOB_SERVICE.queue_positions()
    items = [
        add_job_owner_identity(job_response(str(job["job_id"]), job, positions), job)
        for job in jobs
    ]
    return items, total


COMIC_ROUTE_DEPS = ComicRouteDependencies(
    current_account=lambda request: current_account(request),
    job_for_request=lambda request, job_id: job_for_request(request, job_id),
    job_response=lambda job_id, job: job_response(job_id, job),
    create_job=lambda job_id, payload, owner_user_id: JOB_SERVICE.create(job_id, payload, owner_user_id=owner_user_id),
    retry_job=lambda job_id, **fields: JOB_SERVICE.retry(job_id, **fields),
    reserve_quota=lambda job_id, account: reserve_job_quota(job_id, account),
    release_quota=lambda job_id: TENANT_GOVERNANCE.release(job_id),
    quota_account=comic_quota_account,
    notify_scheduler=notify_scheduler,
    project_root=comic_project_root,
    upload_dir=lambda owner_user_id, job_id: tenant_job_dir(UPLOAD_DIR, owner_user_id, job_id),
    list_projects=list_comic_projects_for_request,
)
app.include_router(create_comic_router(COMIC_ROUTE_DEPS))


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
    # Plain bearer for generated clients (Flutter/Dart): send the access token from
    # /api/v1/auth/token (client=native) as "Authorization: Bearer <token>".
    security_schemes["bearerAuth"] = {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    for path, path_item in schema.get("paths", {}).items():
        if not is_protected_path(path):
            continue
        for method, operation in path_item.items():
            if method.lower() in {"get", "post", "put", "patch", "delete", "options", "head"}:
                operation["security"] = [{"OAuth2PasswordBearer": ["admin"]}, {"bearerAuth": []}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi


@app.get("/googlefffecc69420e3aa8.html", include_in_schema=False)
async def google_verification():
    verification_path = FRONTEND_DIR / "googlefffecc69420e3aa8.html"
    if verification_path.exists():
        return FileResponse(verification_path, media_type="text/html")
    return Response(content="google-site-verification: googlefffecc69420e3aa8.html\n", media_type="text/html")


if FRONTEND_DIR.exists():
    # Registered before the static mount so "/" gets absolute SEO URLs and robots/sitemap exist.
    app.include_router(create_seo_router(FRONTEND_DIR))
    app.mount("/", FrontendStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
