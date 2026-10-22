"""Standalone text → audio: one request synthesises a short text with a chosen TTS provider.

Audio is stored per user under ``<output>/users/<owner>/tts`` and served by id, because the
production CSP blocks ``blob:``/``data:`` media and the player needs a same-origin URL.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from infrastructure.tts_providers import (
    ensure_provider_configured,
    normalize_tts_provider,
    resolve_tts_voice_selection,
    tts_account_blocked,
)

DEFAULT_MAX_CHARS = 5000
_AUDIO_ID = re.compile(r"^[0-9a-f]{32}\.(mp3|wav)$")


def max_text_chars() -> int:
    try:
        return max(100, int(os.environ.get("VIDTRANS_TTS_MAX_CHARS", DEFAULT_MAX_CHARS)))
    except ValueError:
        return DEFAULT_MAX_CHARS


@dataclass
class TextToSpeechDependencies:
    current_account: Callable[[Request], dict[str, Any]]
    # (provider, text, output_path, voice_type, voice_key, speed, language) -> None
    synthesize: Callable[[str, str, Path, str, str | None, float, str], None]
    storage_dir: Callable[[str], Path]
    media_duration: Callable[[Path], float]


class TextToSpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    provider: str = "cit"
    voice_type: str = Field(default="female", pattern="^(female|male)$")
    voice: str = Field(default="", max_length=200)
    speech_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    language: str = Field(default="vi", max_length=12)


def prune_text_to_speech_audio(output_root: Path, max_age_days: int) -> int:
    """Delete generated clips (and abandoned partial files) older than ``max_age_days``."""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for path in Path(output_root).glob("users/*/tts/*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _audio_extension(path: Path) -> str:
    with path.open("rb") as handle:
        return "wav" if handle.read(4) == b"RIFF" else "mp3"


def create_text_to_speech_router(deps: TextToSpeechDependencies) -> APIRouter:
    router = APIRouter(prefix="/api/v1/text-to-speech")

    @router.get("/config")
    def config() -> JSONResponse:
        return JSONResponse({"max_chars": max_text_chars()})

    @router.post("")
    def synthesize(request: Request, body: TextToSpeechRequest) -> JSONResponse:
        owner_user_id = str(deps.current_account(request)["user_id"])
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Văn bản trống")
        if len(text) > max_text_chars():
            raise HTTPException(
                status_code=400,
                detail=f"Văn bản dài {len(text)} ký tự, vượt giới hạn {max_text_chars()} ký tự mỗi lần tạo",
            )
        try:
            provider = normalize_tts_provider(body.provider)
            ensure_provider_configured(provider)
            selection = resolve_tts_voice_selection(provider, body.voice, body.voice)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        directory = deps.storage_dir(owner_user_id)
        audio_id = uuid.uuid4().hex
        partial = directory / f"{audio_id}.part"
        try:
            deps.synthesize(
                provider, text, partial, body.voice_type,
                selection.get(body.voice_type), body.speech_rate, body.language,
            )
            if not partial.is_file() or partial.stat().st_size == 0:
                raise RuntimeError("Nhà cung cấp không trả về âm thanh")
            target = directory / f"{audio_id}.{_audio_extension(partial)}"
            partial.replace(target)
        except Exception as exc:
            partial.unlink(missing_ok=True)
            blocked = tts_account_blocked(exc)
            detail = f"Không tạo được giọng nói: {blocked}" if blocked else f"Không tạo được giọng nói: {exc}"
            raise HTTPException(status_code=502, detail=detail) from exc

        try:
            duration = round(deps.media_duration(target), 2)
        except Exception:
            duration = None
        return JSONResponse({
            "id": target.name,
            "url": f"/api/v1/text-to-speech/{target.name}",
            "download_url": f"/api/v1/text-to-speech/{target.name}?download=1",
            "provider": provider,
            "voice": selection.get(body.voice_type) or body.voice_type,
            "duration": duration,
            "characters": len(text),
        })

    @router.get("/{audio_id}")
    def audio(request: Request, audio_id: str, download: bool = False) -> FileResponse:
        if not _AUDIO_ID.match(audio_id):
            raise HTTPException(status_code=404, detail="file not found")
        owner_user_id = str(deps.current_account(request)["user_id"])
        target = deps.storage_dir(owner_user_id) / audio_id
        if not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        media_type = "audio/wav" if target.suffix == ".wav" else "audio/mpeg"
        return FileResponse(
            target,
            media_type=media_type,
            filename=f"giong-noi-{audio_id[:8]}{target.suffix}" if download else None,
            headers={"Cache-Control": "private, no-store"},
        )

    return router
