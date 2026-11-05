"""HTTP API of the "Cắt clip" feature (a menu of its own, not part of the translate form).

GET  /api/v1/clips/sources               finished jobs whose video can be cut
GET  /api/v1/clips/sources/{job_id}      one source: duration, subtitle cues, cut points
POST /api/v1/clips/plan                  an equal-length plan snapped to cue gaps
POST /api/v1/clips/suggest               AI-picked best moments (paid plans), as an editable plan
POST /api/v1/clips                       queue one clip job per planned clip (one batch)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from application.clip_service import (
    CLIP_JOB_ACTION,
    CROP_NEEDS_CLEAN_SOURCE,
    ClipSource,
    ClipSourceError,
    clip_job_request,
    is_clip_job,
)
from infrastructure.llm_providers import LLMError
from pipeline.clip_suggest import MAX_SUGGESTIONS, MAX_WISH_CHARS, JSONModel, suggest_clips
from pipeline.clip_planning import (
    FRAME_ORIGINAL,
    FRAME_VERTICAL_CROP,
    FRAMES,
    MAX_CLIP_SECONDS,
    MAX_CLIPS,
    MIN_CLIP_SECONDS,
    ClipPlanError,
    cut_points,
    plan_equal_clips,
    validate_plan,
)


@dataclass
class ClipRouteDependencies:
    current_account: Callable[[Request], dict[str, Any]]
    job_for_request: Callable[[Request, str], dict[str, Any] | None]
    # (account, search, limit, offset) -> (completed jobs that have an output video, total)
    list_sources: Callable[[dict[str, Any], str | None, int, int], tuple[list[dict[str, Any]], int]]
    load_source: Callable[[dict[str, Any]], ClipSource]
    create_batch: Callable[[str, str, dict[str, Any], str], None]
    create_job: Callable[[str, dict[str, Any], str], dict[str, Any]]
    attach_to_batch: Callable[[str, str], None]
    job_response: Callable[[str, dict[str, Any]], dict[str, Any]]
    notify_scheduler: Callable[[], None]
    # Why this account cannot get AI suggestions (plan, server setup), or None when it can.
    suggest_unavailable: Callable[[dict[str, Any]], str | None] = lambda account: "AI gợi ý chưa được bật"
    suggest_model: Callable[[], JSONModel] | None = None


class ClipIn(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str = Field(default="", max_length=200)
    frame: str = Field(default=FRAME_ORIGINAL, description=" | ".join(FRAMES))


class CreateClipsIn(BaseModel):
    source_job_id: str = Field(min_length=1, max_length=64)
    clips: list[ClipIn] = Field(min_length=1, max_length=MAX_CLIPS)
    snap: bool = Field(default=True, description="Move cut points to the nearest gap between subtitle cues.")


class SuggestIn(BaseModel):
    source_job_id: str = Field(min_length=1, max_length=64)
    count: int = Field(default=5, ge=1, le=MAX_SUGGESTIONS)
    min_seconds: float = Field(default=15, ge=MIN_CLIP_SECONDS, le=MAX_CLIP_SECONDS)
    max_seconds: float = Field(default=60, ge=MIN_CLIP_SECONDS, le=MAX_CLIP_SECONDS)
    wish: str = Field(default="", max_length=MAX_WISH_CHARS,
                      description="What the user wants, e.g. “các đoạn đánh nhau”.")
    frame: str = Field(default=FRAME_ORIGINAL, description=" | ".join(FRAMES))


class EqualPlanIn(BaseModel):
    source_job_id: str = Field(min_length=1, max_length=64)
    clip_seconds: float = Field(ge=MIN_CLIP_SECONDS, le=MAX_CLIP_SECONDS)
    frame: str = Field(default=FRAME_ORIGINAL, description=" | ".join(FRAMES))


def _source_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "filename": job.get("filename") or job["job_id"],
        "duration": job.get("source_duration"),
        "mode": job.get("mode"),
        "target_language": job.get("target_language"),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at") or job.get("finished_at"),
        "video_url": f"/api/v1/jobs/{job['job_id']}/artifacts/video",
        "has_subtitles": bool(job.get("translation_file")),
    }


def create_clip_router(deps: ClipRouteDependencies) -> APIRouter:
    router = APIRouter(prefix="/api/v1/clips", tags=["Clips"])

    def source_for(request: Request, job_id: str) -> ClipSource:
        job = deps.job_for_request(request, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy tiến trình nguồn")
        try:
            return deps.load_source({**job, "job_id": job_id})
        except ClipSourceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/sources")
    def list_clip_sources(
        request: Request,
        search: str | None = Query(default=None, max_length=200),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        account = deps.current_account(request)
        jobs, total = deps.list_sources(account, search, limit, offset)
        return JSONResponse({"items": [_source_summary(job) for job in jobs], "total": total,
                             "limit": limit, "offset": offset})

    @router.get("/sources/{job_id}")
    def get_clip_source(request: Request, job_id: str) -> JSONResponse:
        source = source_for(request, job_id)
        suggest_reason = deps.suggest_unavailable(deps.current_account(request))
        if suggest_reason is None and not any(str(segment.get("text") or "").strip() for segment in source.segments):
            suggest_reason = "Video này không có phụ đề nên AI không đọc được nội dung để gợi ý"
        return JSONResponse({
            "job_id": source.job_id,
            "filename": source.filename,
            "duration": source.duration,
            "video_url": f"/api/v1/jobs/{source.job_id}/artifacts/video",
            "cues": [
                {"start": float(segment["start"]), "end": float(segment["end"]), "text": str(segment.get("text") or "")}
                for segment in source.segments
                if "start" in segment and "end" in segment and not segment.get("skip_subtitle")
            ],
            "cut_points": cut_points(source.segments, source.duration),
            # The 9:16 crop draws subtitles again from the clean source; without subtitles it crops the video.
            "can_crop": source.clean_video is not None or not source.segments,
            "ai_suggest": {"available": suggest_reason is None, "reason": suggest_reason},
            "limits": {"min_clip_seconds": MIN_CLIP_SECONDS, "max_clip_seconds": MAX_CLIP_SECONDS,
                       "max_clips": MAX_CLIPS, "frames": list(FRAMES)},
        })

    @router.post("/plan")
    def plan_equal(request: Request, body: EqualPlanIn) -> JSONResponse:
        source = source_for(request, body.source_job_id)
        if body.frame not in FRAMES:
            raise HTTPException(status_code=400, detail="Khung hình không hợp lệ")
        try:
            clips = plan_equal_clips(source.segments, source.duration, body.clip_seconds)
        except ClipPlanError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"clips": [{**clip.as_dict(), "frame": body.frame} for clip in clips]})

    @router.post("/suggest")
    def suggest(request: Request, body: SuggestIn) -> JSONResponse:
        reason = deps.suggest_unavailable(deps.current_account(request))
        if reason is not None:
            raise HTTPException(status_code=403, detail=reason)
        source = source_for(request, body.source_job_id)
        if body.frame not in FRAMES:
            raise HTTPException(status_code=400, detail="Khung hình không hợp lệ")
        if body.frame == FRAME_VERTICAL_CROP and source.segments and source.clean_video is None:
            raise HTTPException(status_code=409, detail=CROP_NEEDS_CLEAN_SOURCE)
        if deps.suggest_model is None:
            raise HTTPException(status_code=503, detail="AI gợi ý chưa được cấu hình trên máy chủ")
        try:
            model = deps.suggest_model()
            clips = suggest_clips(model, source.segments, source.duration, count=body.count,
                                  min_seconds=min(body.min_seconds, body.max_seconds),
                                  max_seconds=max(body.min_seconds, body.max_seconds),
                                  wish=body.wish, frame=body.frame)
        except ClipPlanError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LLMError as exc:
            status = 429 if exc.rate_limited else 503 if exc.unavailable else 502
            raise HTTPException(status_code=status, detail=f"AI gợi ý tạm lỗi: {exc}") from exc
        return JSONResponse({"clips": [clip.as_dict() for clip in clips], "provider": getattr(model, "name", "")})

    @router.post("", status_code=201)
    def create_clips(request: Request, body: CreateClipsIn) -> JSONResponse:
        account = deps.current_account(request)
        job = deps.job_for_request(request, body.source_job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy tiến trình nguồn")
        source = source_for(request, body.source_job_id)
        try:
            clips = validate_plan([clip.model_dump() for clip in body.clips], source.duration,
                                  segments=source.segments, snap=body.snap)
        except ClipPlanError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if source.segments and source.clean_video is None and any(clip.frame == FRAME_VERTICAL_CROP for clip in clips):
            raise HTTPException(status_code=409, detail=CROP_NEEDS_CLEAN_SOURCE)
        # Clips belong to the source's owner, also when an admin cuts them.
        owner_user_id = str(job.get("owner_user_id") or account["user_id"])
        batch_id = f"batch_{uuid.uuid4().hex[:10]}"
        source_name = " ".join(source.filename.split())[:90]
        deps.create_batch(batch_id, f"Cắt clip · {source_name}"[:120],
                          {"job_action": CLIP_JOB_ACTION, "source_job_id": source.job_id}, owner_user_id)
        created = []
        for index, clip in enumerate(clips, start=1):
            job_id = uuid.uuid4().hex[:8]
            request_payload = clip_job_request(source, clip, index, len(clips))
            stored = deps.create_job(job_id, {
                "status": "queued", "step": "queued", "progress": 0.0,
                "step_detail": "Đang chờ cắt clip",
                "job_action": CLIP_JOB_ACTION, "job_type": CLIP_JOB_ACTION,
                "filename": f"{clip.title} · {source_name}"[:160],
                "target_language": job.get("target_language"),
                "mode": job.get("mode"),
                "clip": request_payload["clip"] | {"source_job_id": source.job_id},
                "resume_request": request_payload,
            }, owner_user_id)
            deps.attach_to_batch(job_id, batch_id)
            created.append(deps.job_response(job_id, {**stored, "batch_id": batch_id}))
        deps.notify_scheduler()
        return JSONResponse({"batch_id": batch_id, "jobs": created}, status_code=201)

    return router


__all__ = ["ClipRouteDependencies", "create_clip_router", "is_clip_job"]
