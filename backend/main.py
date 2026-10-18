import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
import textwrap
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, quote_plus, urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from infrastructure.frontend_static import FrontendStaticFiles
from gtts import gTTS
from starlette.background import BackgroundTask
from infrastructure.artifact_archive import build_artifact_archive

from app.config import AppSettings
from application.job_scheduler import JobScheduler
from application.job_service import JobService
from application.instagram_routes import create_instagram_router
from application.threads_routes import create_threads_router
from application.social_publish_routes import create_social_publish_router
from application.social_account_routes import create_social_account_router
from application.social_connection_routes import create_social_connection_router
from application.social_publishing import SocialPublishScheduler, SocialPublishingService
from application.tiktok_browser_routes import create_tiktok_browser_router
from application.tiktok_publish_routes import create_tiktok_publish_router
from application.tiktok_publishing import TikTokPublishScheduler, TikTokPublishingService
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
from infrastructure.payments import (
    PLAN_CATALOG,
    PaymentConfig,
    PaymentError,
    PaymentGatewayClient,
    PaymentOrderStore,
)
from infrastructure.job_events import ALL_JOBS, JobEventHub
from infrastructure.job_store import SQLiteJobStore
from infrastructure.publishers.registry import PublisherRegistry
from infrastructure.publishers.instagram import InstagramPublisher
from infrastructure.publishers.tiktok import TikTokSocialPublisher
from infrastructure.publishers.threads import ThreadsPublisher
from infrastructure.publishers.pinterest import PinterestPublisher
from infrastructure.publishers.linkedin import LinkedInPublisher
from infrastructure.publishers.facebook import FacebookPublisher, FacebookPublisherError
from infrastructure.publishers.youtube import YouTubePublisher, YouTubePublisherError
from infrastructure.publishers.encrypted_oauth_store import EncryptedOAuthAccountStore
from infrastructure.credential_cipher import CredentialCipher, CredentialCipherError
from infrastructure.signed_media import SignedMediaUrlService
from infrastructure.social_publish_store import SQLiteSocialPublishStore
from infrastructure.social_credentials import SocialCredentialStore
from infrastructure.tenant_governance import QuotaExceededError, TenantGovernanceStore
from infrastructure.tiktok_publish_store import SQLiteTikTokPublishStore
from infrastructure.media_validation import InvalidVideoError, public_error_message, validate_video_file
from infrastructure.artifact_io import copy_file_atomic, write_json_atomic
from infrastructure.media_probe import MediaProbe, probe_media_file
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
from infrastructure.tiktok_publisher import TikTokPublisher, TikTokPublisherError
from pipeline.ocr import OCRConfig, annotate_ocr_segments_with_asr, extract_burned_subtitle_segments
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
from pipeline.tiktok import LocalExtractiveTikTokProvider, write_tiktok_artifacts
from pipeline.asr import ASRConfig, ASRService
from pipeline.translation import (
    ensure_translation_complete,
    filter_meaningful_segments,
    segment_filter_signature,
    translate_segments,
)
from pipeline.tts_timing import (
    adaptive_synthesis_rate_percent,
    available_voice_window,
    bounded_tempo,
    schedule_voice_segments,
)
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

# job_change_snapshot is defined with the job routes below; it is only
# called once the broadcaster runs, after the module has loaded.
JOB_EVENTS = JobEventHub(lambda job_ids: job_change_snapshot(job_ids))
JOB_SERVICE = JobService(SQLiteJobStore(WORK_DIR / "jobs.sqlite3"), on_change=JOB_EVENTS.notify)
N8N_WORKFLOW_CONFIG = N8nWebhookConfig.from_env()
WORKFLOW_EVENT_STORE = WorkflowEventStore(WORK_DIR / "jobs.sqlite3")
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
                    account_store=tenant_encrypted_oauth_store(owner_user_id, "INSTAGRAM"),
                ),
                ThreadsPublisher(tenant_social_auth_dir(owner_user_id, "threads"), SIGNED_MEDIA_URLS),
                PINTEREST_PUBLISHER,
                LINKEDIN_PUBLISHER,
                FacebookPublisher(
                    tenant_social_auth_dir(owner_user_id, "facebook"),
                    account_store=tenant_encrypted_oauth_store(owner_user_id, "FACEBOOK"),
                ),
                YouTubePublisher(
                    tenant_social_auth_dir(owner_user_id, "youtube"),
                    account_store=tenant_encrypted_oauth_store(owner_user_id, "YOUTUBE"),
                ),
                TikTokSocialPublisher(tiktok),
            ])
            _TENANT_SOCIAL_REGISTRIES[owner_user_id] = registry
        return registry


TIKTOK_PUBLISHER = TikTokPublisher(WORK_DIR / "tiktok-auth")
TIKTOK_BROWSER = TikTokBrowserManager(WORK_DIR / "tiktok-browser")
TIKTOK_PUBLISH_STORE = SQLiteTikTokPublishStore(WORK_DIR / "jobs.sqlite3")
TIKTOK_PUBLISHING = TikTokPublishingService(
    TIKTOK_PUBLISH_STORE,
    TIKTOK_PUBLISHER,
    publisher_resolver=lambda job_id: tenant_tiktok_publisher(job_owner_user_id(job_id)),
)
app.include_router(create_tiktok_browser_router(
    TIKTOK_BROWSER,
    JOB_SERVICE,
    OUTPUT_DIR,
    TIKTOK_PUBLISHER,
    owner_resolver=lambda request: str(current_account(request)["user_id"]),
    publisher_resolver=lambda request: tenant_tiktok_publisher(str(current_account(request)["user_id"])),
))
app.include_router(create_tiktok_publish_router(
    TIKTOK_PUBLISHING,
    JOB_SERVICE,
    OUTPUT_DIR,
    owner_resolver=lambda request: str(current_account(request)["user_id"]),
    connection_resolver=lambda user_id, connection_id, platform: resolve_social_connection(
        user_id, connection_id, platform
    ),
))
SOCIAL_PUBLISH_STORE = SQLiteSocialPublishStore(WORK_DIR / "jobs.sqlite3")
_migrated_tiktok_history = SOCIAL_PUBLISH_STORE.migrate_legacy_tiktok_history()
if _migrated_tiktok_history:
    logger.info("Migrated %d terminal TikTok publish attempts", _migrated_tiktok_history)
SIGNED_MEDIA_URLS = SignedMediaUrlService(
    OUTPUT_DIR,
    public_base_url=os.environ.get("VIDTRANS_PUBLIC_BASE_URL", "").strip(),
    secret=os.environ.get("VIDTRANS_MEDIA_SIGNING_SECRET", "").strip(),
)
INSTAGRAM_PUBLISHER = InstagramPublisher(
    WORK_DIR / "instagram-auth",
    SIGNED_MEDIA_URLS,
)
THREADS_PUBLISHER = ThreadsPublisher(WORK_DIR / "threads-auth", SIGNED_MEDIA_URLS)
PINTEREST_PUBLISHER = PinterestPublisher()
LINKEDIN_PUBLISHER = LinkedInPublisher()
FACEBOOK_PUBLISHER = FacebookPublisher(WORK_DIR / "facebook-auth")
YOUTUBE_PUBLISHER = YouTubePublisher(WORK_DIR / "youtube-auth")
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
    WORK_DIR / "jobs.sqlite3",
    AUTH_MANAGER.config.username if AUTH_MANAGER.config.enabled else "admin",
    AUTH_MANAGER.config.password_hash if AUTH_MANAGER.config.enabled else "",
)
_bootstrap_login = AUTH_MANAGER.config.username or "admin"
_bootstrap_email = _bootstrap_login if "@" in _bootstrap_login else f"{_bootstrap_login}@local.vidtrans"
_bootstrap_owner = ADMIN_ACCOUNTS.get_by_email(_bootstrap_email)
if _bootstrap_owner is None:
    raise RuntimeError("Bootstrap owner account is missing")
JOB_SERVICE.backfill_legacy_owner(str(_bootstrap_owner["user_id"]))


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
SOCIAL_CREDENTIALS = SocialCredentialStore(WORK_DIR / "jobs.sqlite3")
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


def register_tenant_oauth_session(request: Request, platform: str, authorization_url: str) -> None:
    state = (parse_qs(urlsplit(authorization_url).query).get("state") or [""])[0]
    if not state:
        raise HTTPException(status_code=503, detail="Nhà cung cấp OAuth không trả về state")
    SOCIAL_CREDENTIALS.create_oauth_session(
        user_id=str(current_account(request)["user_id"]),
        platform=platform,
        state=state,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        return_path="/#tiktok",
    )


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
PAYMENT_ORDERS = PaymentOrderStore(WORK_DIR / "jobs.sqlite3")
PAYMENT_GATEWAY = PaymentGatewayClient(PAYMENT_CONFIG)
TENANT_GOVERNANCE = TenantGovernanceStore(WORK_DIR / "jobs.sqlite3")
ASR_SERVICE = ASRService(ASRConfig.from_env(), cpu_threads=int(os.environ.get("VIDTRANS_ASR_CPU_THREADS", "0")))
_whisper_slots = threading.BoundedSemaphore(SETTINGS.whisper_concurrency)
_ocr_slots = threading.BoundedSemaphore(SETTINGS.ocr_concurrency)
_job_context = threading.local()


class JobCancelled(RuntimeError):
    pass


PUBLIC_AUTH_PATHS = {
    "/api/v1/auth/status",
    "/api/v1/auth/token",
    "/api/v1/auth/register",
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
    for segment in segments:
        if segment.get("skip_subtitle"):
            continue
        text = wrap_text(segment["text"])
        if not text:
            continue
        srt_index += 1
        blocks.append(
            "\n".join(
                [
                    str(srt_index),
                    f"{format_time(segment.get('tts_start', segment['start']))} --> "
                    f"{format_time(segment.get('tts_end', segment['end']))}",
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
        if segment.get("skip_subtitle"):
            continue
        render_start = segment.get("tts_start", segment["start"])
        render_end = segment.get("tts_end", segment["end"])
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
) -> tuple[list[dict[str, Any]], str | None, float | None]:
    progress = make_asr_progress_callback(job_id, media_duration) if job_id else None
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
    if current and current.get("cancel_requested"):
        if fields.get("status") == "processing":
            fields.pop("status")
        elif fields.get("status") == "completed":
            raise JobCancelled("Job đã được yêu cầu hủy trước khi hoàn tất")
    updated = JOB_SERVICE.update(job_id, **fields)
    if N8N_WORKFLOW_CONFIG.enabled:
        WORKFLOW_EVENTS.emit_transition(job_id, current, updated)
        notify_workflow_dispatcher()


def record_job_stage_measurement(job_id: str, measurement: StageMeasurement) -> None:
    """Persist additive, content-free stage metrics in the existing job payload."""

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


async def tts_edge_sync(
    text: str, output_path: Path, voice_name: str, rate: str, pitch: str = "+0Hz"
) -> None:
    if edge_tts is None:
        raise RuntimeError("edge-tts is not available")
    communicate = edge_tts.Communicate(text=text, voice=voice_name, rate=rate, pitch=pitch)
    await communicate.save(str(output_path))


def tts_gtts_sync(text: str, output_path: Path, slow: bool = False, language: str = "vi") -> None:
    gTTS(text=text, lang=language_profile(language).gtts_code, slow=slow).save(str(output_path))


def synthesize_tts_segments(
    segments: list[dict[str, Any]],
    work_dir: Path,
    voice_type: str,
    speech_rate: float,
    voice_style: str = "natural",
    job_id: str | None = None,
    target_language: str = "vi",
) -> list[Path]:
    tts_dir = work_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    preset = voice_preset(voice_style)
    rate_percent = int((speech_rate - 1.0) * 100) + preset.rate_percent
    base_rate_string = f"{rate_percent:+d}%"
    pitch_string = f"{preset.pitch_hz:+d}Hz"

    import concurrent.futures
    import threading
    completed_count = 0
    total_valid = sum(1 for s in segments if not s.get("skip_tts") and " ".join(s["text"].split()))
    progress_lock = threading.Lock()

    def process_segment(index: int, segment: dict[str, Any]) -> Path | None:
        nonlocal completed_count
        if job_id:
            ensure_job_active(job_id)

        # Skip non-meaningful segments (filler words, hallucinations, etc.)
        if segment.get("skip_tts"):
            return None

        # Clean text
        raw_text = " ".join(segment["text"].split())
        if not raw_text:
            return None

        # Optimize Translation text pacing using Original Word Timestamps
        # If the original Chinese text had long gaps between words, we insert commas
        # in the translated text to force edge-tts to pause naturally.
        text = raw_text
        words = segment.get("words", [])
        if words and len(words) > 1:
            seg_start = float(segment.get("start", 0))
            seg_end = float(segment.get("end", seg_start + 1))
            seg_dur = max(0.1, seg_end - seg_start)

            chars = list(text)
            inserts = []
            for i in range(len(words) - 1):
                gap = float(words[i+1].get("start", 0)) - float(words[i].get("end", 0))
                if gap > 0.4:
                    # Map the temporal pause to character position proportionally
                    ratio = (float(words[i].get("end", 0)) - seg_start) / seg_dur
                    char_idx = min(len(chars) - 1, max(0, int(ratio * len(chars))))
                    # Find nearest space to insert the punctuation
                    offset = 0
                    best_idx = char_idx
                    while offset < 15:
                        if char_idx + offset < len(chars) and chars[char_idx + offset] == " ":
                            best_idx = char_idx + offset
                            break
                        if char_idx - offset >= 0 and chars[char_idx - offset] == " ":
                            best_idx = char_idx - offset
                            break
                        offset += 1
                    # Avoid double punctuation
                    if best_idx > 0 and chars[best_idx - 1] not in (",", ".", "!", "?", ":", ";"):
                        inserts.append((best_idx, ","))

            for idx, punct in sorted(inserts, reverse=True):
                chars.insert(idx, punct)
            text = "".join(chars)

        output_path = tts_dir / f"{index:04d}.mp3"
        selected_voice_type = str(segment.get("voice_type", voice_type))
        profile = language_profile(target_language)
        voice_name = profile.edge_voice(selected_voice_type)

        # Pass 1: generate at the requested expressive rate and measure it.
        # When a line is too long, a second native TTS render is preferable to
        # a large FFmpeg atempo shift, which flattens Vietnamese prosody.
        edge_succeeded = False
        effective_rate_percent = rate_percent
        try:
            asyncio.run(tts_edge_sync(text, output_path, voice_name, base_rate_string, pitch_string))
            edge_succeeded = True
        except Exception as exc:
            logger.warning("edge-tts failed, fallback to gTTS: %s", exc)
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
        run_ffmpeg(
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
        applied_native_rate_boost = 0
        if edge_succeeded and native_rate_boost:
            adaptive_output_path = tts_dir / f"{index:04d}_adaptive.mp3"
            adaptive_trimmed_path = tts_dir / f"{index:04d}_adaptive.wav"
            candidate_rate_percent = rate_percent + native_rate_boost
            candidate_rate_string = f"{candidate_rate_percent:+d}%"
            try:
                asyncio.run(
                    tts_edge_sync(
                        text, adaptive_output_path, voice_name, candidate_rate_string, pitch_string
                    )
                )
                run_ffmpeg(
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
            except Exception as exc:
                logger.warning("Adaptive edge-tts render failed; keeping first pass: %s", type(exc).__name__)
            finally:
                adaptive_output_path.unlink(missing_ok=True)
                adaptive_trimmed_path.unlink(missing_ok=True)

        speed_factor = bounded_tempo(rendered_duration, cue_duration)
        fitted_path = trimmed_path
        if speed_factor > 1.0:
            fitted_path = tts_dir / f"{index:04d}_fitted.wav"
            run_ffmpeg(
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
    schedule_voice_segments(segments, video_duration=video_duration)
    input_index = 0
    for segment in segments:
        tts_path = segment.get("tts_path")
        if not tts_path:
            continue
        tts_start = float(segment.get("tts_start", segment["start"]))
        if tts_start >= video_duration:
            continue
        delay_ms = max(0, int(tts_start * 1000))
        input_args.extend(["-i", str(tts_path)])
        filter_parts.append(
            f"[{input_index}:a]adelay={delay_ms}|{delay_ms},volume=1.4[a{input_index}]"
        )
        labels.append(f"[a{input_index}]")
        input_index += 1
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

    if voice_track:
        input_args.extend(["-i", str(voice_track)])
        filter_parts.append(f"[{input_index}:a]volume=1.0,alimiter=limit=0.95[v]")
        labels.append("[v]")
        input_index += 1

    source_has_audio = source_probe.has_audio if source_probe is not None else has_audio_stream(video_path)
    if keep_original_audio and source_has_audio:
        original_audio = extract_original_audio(video_path, work_dir / "original_audio.wav")
        input_args.extend(["-i", str(original_audio)])
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
        if segment.get("skip_subtitle"):
            continue
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
    frame_paths = sorted(frames_dir.glob("frame_*.png"))

    for index, frame_path in enumerate(frame_paths):
        timestamp = index / fps
        active = next(
            (seg for seg in segments if not seg.get("skip_subtitle") and seg["start"] <= timestamp <= seg["end"]),
            None,
        )
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
    video_fps: float | None = None,
) -> None:
    subtitle_filter = f"subtitles=filename='{ff_escape_path(subtitle_path)}'"
    if BUNDLED_FONT.is_file():
        # Make the bundled CJK font available for libass fallback, but honor
        # the FontName written in the ASS file. Forcing CJK here breaks all
        # Vietnamese glyphs even when the chosen style supports them.
        subtitle_filter += f":fontsdir='{ff_escape_path(FONT_DIR)}'"
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
) -> tuple[list[dict[str, Any]], str | None, float | None, str, int]:
    """Run OCR/ASR and return filtered, checkpoint-ready source segments."""

    video_duration = source_probe.duration
    ocr_segments: list[dict[str, Any]] = []
    ocr_requested = subtitle_source in {"auto", "burned"} and source_language == "zh-CN"
    if ocr_requested and not SETTINGS.paddle_ocr_enabled:
        message = "OCR is disabled on this Linux ARM64 runtime; using Whisper speech recognition"
        if subtitle_source == "burned":
            raise RuntimeError(
                "Burned-subtitle OCR is disabled on this runtime because PaddleOCR crashes the server. "
                "Choose Whisper or Auto, or set VIDTRANS_ENABLE_PADDLE_OCR=1 only after validating PaddleOCR."
            )
        logger.warning(message)
        update_job(job_id, status="processing", step="transcribing", step_detail=message, progress=0.22)
    elif ocr_requested:
        update_job(job_id, status="processing", step="extracting-subtitles", progress=0.12)
        try:
            with measure_job_stage(job_id, "ocr", video_duration=video_duration):
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
    update_job(
        job_id,
        status="processing",
        step="transcribing",
        step_detail=asr_progress_detail(0, video_duration),
        progress=0.22,
    )
    asr_segments: list[dict[str, Any]] = []
    detected_language: str | None = None
    detected_language_probability: float | None = None
    with measure_job_stage(job_id, "asr", video_duration=video_duration):
        if source_probe.has_audio:
            with _whisper_slots:
                ensure_job_active(job_id)
                asr_segments, detected_language, detected_language_probability = transcribe_video(
                    whisper_model, video_path, source_language, job_id, video_duration
                )
        elif not ocr_segments:
            raise RuntimeError(
                "Video có hình ảnh nhưng không có âm thanh để nhận diện giọng nói. "
                "Nếu video có phụ đề sẵn, hãy chọn OCR và bật OCR trên máy chủ; "
                "nếu không, hãy chọn video có âm thanh."
            )
    if ocr_segments:
        segments = annotate_ocr_segments_with_asr(ocr_segments, asr_segments)
        source_method = "ocr"
    else:
        segments = asr_segments
        source_method = "speech"
    if not segments:
        raise RuntimeError(
            "Không nhận diện được phụ đề hoặc lời thoại nguồn. "
            "Kiểm tra vùng OCR, ngôn ngữ nguồn hoặc thử Whisper model lớn hơn."
        )

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
) -> None:
    _job_context.job_id = job_id
    current_job = JOB_SERVICE.get(job_id) or {}
    owner_user_id = str(current_job.get("owner_user_id") or _bootstrap_owner["user_id"])
    work_dir = tenant_job_dir(WORK_DIR, owner_user_id, job_id)
    output_dir = tenant_job_dir(OUTPUT_DIR, owner_user_id, job_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    subtitle_style = {**DEFAULT_SUB_STYLE, **(subtitle_style or {})}
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
                "version": 1,
                "source_checksum": source_checksum,
                "subtitle_source": subtitle_source,
                "source_language": source_language,
                "whisper_model": whisper_model,
                "asr_engine": ASR_SERVICE.engine_name,
                "asr_compute_type": ASR_SERVICE.config.compute_type,
                "asr_beam_size": ASR_SERVICE.config.beam_size,
                "asr_vad": ASR_SERVICE.config.vad,
                "asr_vad_min_silence_ms": ASR_SERVICE.config.vad_min_silence_ms,
                "ocr_enabled": SETTINGS.paddle_ocr_enabled,
                "ocr_sample_fps": ocr_sample_fps,
                "ocr_roi_top": ocr_roi_top,
                "ocr_roi_bottom": ocr_roi_bottom,
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
        segments = list(transcript_payload["segments"])
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
                "translation_pipeline_version": 1,
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
                translated_segments = translate_segments(
                    segments,
                    source_language=effective_source_language,
                    target_language=target_language,
                    check_active=lambda: ensure_job_active(job_id),
                    progress_callback=lambda completed, total: update_job(
                        job_id,
                        progress=0.4 + 0.06 * completed / max(1, total),
                        step_detail=f"Đã kiểm tra bản dịch {completed}/{total} câu",
                    ),
                )
        record_job_stage_cache(job_id, "translation", translation_cache_hit)
        if translation_cache_hit:
            update_job(job_id, step_detail="Đã dùng lại bản dịch hợp lệ", progress=0.46)
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

        audio_path = None
        tts_checkpoint = None
        audio_checkpoint = None
        if mode in (2, 3):
            ensure_job_active(job_id)
            update_job(job_id, step="tts", progress=0.55)
            tts_fingerprint = fingerprint_payload(
                {
                    "version": 1,
                    "translation_bundle_checksum": translation_checkpoint.bundle_checksum,
                    "source_checksum": source_checksum,
                    "target_language": target_language,
                    "voice_type": voice_type,
                    "voice_mode": voice_mode,
                    "voice_style": voice_style,
                    "speech_rate": speech_rate,
                    "video_duration": round(video_duration, 3),
                    "routing": [
                        {
                            "voice_type": segment.get("voice_type"),
                            "voice_routing": segment.get("voice_routing"),
                        }
                        for segment in translated_segments
                    ],
                    "tts_pipeline_version": 2,
                }
            )
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
                    synthesize_tts_segments(
                        translated_segments,
                        tts_work_dir,
                        voice_type,
                        speech_rate,
                        voice_style,
                        job_id=job_id,
                        target_language=target_language,
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
                    "audio_mix_version": 1,
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

        # Write subtitle artifacts only after TTS planning. In dubbing modes,
        # tts_start/tts_end make the visible Vietnamese line follow the natural
        # voice instead of the much shorter source-language cue.
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
                "render_pipeline_version": 1,
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
                publish_result = tenant_tiktok_publisher(owner_user_id).publish(
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
        TENANT_GOVERNANCE.consume(job_id)
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
    except (KeyError, TypeError, ValueError, FileNotFoundError, SocialVideoDownloadError) as exc:
        logger.warning("Unable to resume job %s: %s", job_id, exc)
        update_job(
            job_id,
            status="failed",
            step="failed",
            error=public_error_message(exc),
            finished_at=utc_now(),
        )


def retention_days(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        logger.warning("Invalid %s; using %d days", name, default)
        return default


def retention_enabled() -> bool:
    return os.environ.get("VIDTRANS_RETENTION_ENABLED", "0").lower() in {"1", "true", "yes"}


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
    apply_retention_policy()
    app.state.retention_task = asyncio.create_task(run_retention_loop())
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
    publish_scheduler = TikTokPublishScheduler(TIKTOK_PUBLISHING)
    app.state.tiktok_publish_scheduler = publish_scheduler
    await publish_scheduler.start()
    social_publish_scheduler = SocialPublishScheduler(SOCIAL_PUBLISHING)
    app.state.social_publish_scheduler = social_publish_scheduler
    await social_publish_scheduler.start()
    app.state.n8n_workflow_dispatcher = N8N_WORKFLOW_DISPATCHER
    await N8N_WORKFLOW_DISPATCHER.start()


@app.on_event("shutdown")
async def stop_job_scheduler() -> None:
    await JOB_EVENTS.stop()
    retention_task = getattr(app.state, "retention_task", None)
    if retention_task is not None:
        retention_task.cancel()
    scheduler = getattr(app.state, "job_scheduler", None)
    if scheduler is not None:
        await scheduler.stop()
    publish_scheduler = getattr(app.state, "tiktok_publish_scheduler", None)
    if publish_scheduler is not None:
        await publish_scheduler.stop()
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
    # Safely clean up user-specific storage directories
    for base_dir in (WORK_DIR, UPLOAD_DIR, OUTPUT_DIR):
        user_dir = base_dir / "users" / storage_owner_segment(user_id)
        if user_dir.exists() and user_dir.is_relative_to(base_dir.resolve()):
            shutil.rmtree(user_dir, ignore_errors=True)
    return JSONResponse({"deleted": True, "user_id": user_id}, headers={"Cache-Control": "no-store"})


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
        ADMIN_ACCOUNTS.update(updated["user_id"], plan=updated["plan"], payment_status="active")
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
    actor = current_account(request)
    try:
        order = PAYMENT_ORDERS.get(order_id)
        if actor["role"] not in {"owner", "admin"} and order["user_id"] != actor["user_id"]:
            raise HTTPException(status_code=403, detail="Bạn không có quyền xác nhận đơn này")
        updated, _ = PAYMENT_ORDERS.mark_paid(
            order_id,
            event_key=f"billing-confirm:{order_id}",
            payload=f"confirmed-by:{actor['user_id']}".encode("utf-8"),
            provider_transaction_ref=f"confirm:{actor.get('email') or actor.get('username') or actor.get('user_id')}",
        )
        ADMIN_ACCOUNTS.update(updated["user_id"], plan=updated["plan"], payment_status="active")
    except PaymentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(updated, headers={"Cache-Control": "no-store"})


@app.post("/api/v1/billing/orders/{order_id}/cancel")
def cancel_billing_order(request: Request, order_id: str) -> JSONResponse:
    actor = current_account(request)
    try:
        order = PAYMENT_ORDERS.get(order_id)
        if actor["role"] not in {"owner", "admin"} and order["user_id"] != actor["user_id"]:
            raise HTTPException(status_code=403, detail="Bạn không có quyền hủy đơn này")
        updated = PAYMENT_ORDERS.cancel(order_id)
    except PaymentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(updated, headers={"Cache-Control": "no-store"})


@app.post("/api/v1/billing/switch-free")
def switch_free_plan(request: Request) -> JSONResponse:
    account = current_account(request)
    ADMIN_ACCOUNTS.update(account["user_id"], plan="free", payment_status="exempt")
    return JSONResponse({"status": "success", "plan": "free"}, headers={"Cache-Control": "no-store"})



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
) -> JSONResponse:
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
    scope = "admin" if account["role"] in {"owner", "admin"} else "user"
    token, expires_in = AUTH_MANAGER.issue_token(
        account["email"], user_id=account["user_id"], role=account["role"], scope=scope
    )
    response = JSONResponse(
        {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": expires_in,
            "scope": scope,
            "role": account["role"],
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


@app.post("/process-video")
@app.post("/convert")
async def process_video_endpoint(
    http_request: Request,
    file: UploadFile = File(...),
    mode: int = Form(...),
    voice_type: str = Form("female"),
    voice_mode: str = Form("auto"),
    voice_style: str = Form("natural"),
    speech_rate: float = Form(1.0),
    background_music: UploadFile | None = File(default=None),
    whisper_model: str = Form("base"),
    keep_original_audio: bool = Form(True),
    original_audio_volume: float = Form(0.18),
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
    speech_rate: float = Form(1.0),
    background_music: UploadFile | None = File(default=None),
    whisper_model: str = Form("base"),
    keep_original_audio: bool = Form(True),
    original_audio_volume: float = Form(0.18),
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
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    account = current_account(request)
    jobs, total = JOB_SERVICE.list_jobs(
        owner_user_id=None if can_manage_all_jobs(account) else str(account["user_id"]),
        status=status,
        batch_id=batch_id,
        search=search,
        limit=limit,
        offset=offset,
    )
    positions = JOB_SERVICE.queue_positions()
    items = [
        add_job_owner_identity(job_response(str(job["job_id"]), job, positions), job)
        for job in jobs
    ]
    return JSONResponse({"items": items, "total": total, "limit": limit, "offset": offset})


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

    Browsers cannot attach an Authorization header to a WebSocket, so only the
    session cookie is accepted. Because cookies ride along on cross-site
    handshakes, the Origin must match the Host the page was served from.
    """

    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "").rsplit(":", 1)[0].strip("[]").lower()
    if origin and (urlsplit(origin).hostname or "").lower() != host:
        return None
    if not AUTH_MANAGER.config.enabled:
        return {}
    if not AUTH_MANAGER.config.configured:
        return None
    token = websocket.cookies.get(AUTH_MANAGER.config.cookie_name)
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

    resume_request = source.get("resume_request")
    if not isinstance(resume_request, dict):
        raise HTTPException(status_code=409, detail="job không có cấu hình retry")
    video_path = Path(str(resume_request.get("video_path", "")))
    source_url = resume_request.get("source_url")
    if not video_path.is_file() and not source_url:
        raise HTTPException(status_code=409, detail="file video nguồn không còn tồn tại")

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
    }

    for key in ("output_video", "subtitle_file", "translation_file", "tiktok_json_file", "tiktok_text_file"):
        filename = source.get(key)
        if filename:
            target = stored_artifact_path(str(filename))
            if target.is_relative_to(OUTPUT_DIR.resolve()):
                target.unlink(missing_ok=True)
        update_payload[key] = None

    JOB_SERVICE.update(job_id, **update_payload)
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
    for key in ("output_video", "subtitle_file", "translation_file", "tiktok_json_file", "tiktok_text_file"):
        filename = job.get(key)
        if filename:
            target = stored_artifact_path(str(filename))
            if target.is_relative_to(OUTPUT_DIR.resolve()):
                target.unlink(missing_ok=True)
    resume_request = job.get("resume_request")
    if isinstance(resume_request, dict):
        for source_key in ("video_path", "source_cookie_path"):
            source_value = resume_request.get(source_key)
            if not source_value:
                continue
            source_path = Path(str(source_value)).resolve()
            if source_path.is_relative_to(UPLOAD_DIR.resolve()):
                source_path.unlink(missing_ok=True)
    job_work_dir = tenant_job_dir(WORK_DIR, str(job.get("owner_user_id") or _bootstrap_owner["user_id"]), job_id)
    if job_work_dir.is_relative_to(WORK_DIR.resolve()) and job_work_dir.exists():
        shutil.rmtree(job_work_dir)
    JOB_SERVICE.delete(job_id)
    TENANT_GOVERNANCE.release(job_id)
    return JSONResponse({"job_id": job_id, "deleted": True})


@app.get("/status/{job_id}")
def get_status(request: Request, job_id: str) -> JSONResponse:
    job = job_for_request(request, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(job_response(job_id, job, JOB_SERVICE.queue_positions()))


@app.get("/download/{filename}")
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
    app.mount("/", FrontendStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
