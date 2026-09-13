from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException

from pipeline.tiktok import LocalExtractiveTikTokProvider
from infrastructure.tiktok_browser import TikTokBrowserError, TikTokBrowserManager


def create_tiktok_browser_router(manager: TikTokBrowserManager, jobs, output_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["TikTok Browser"])
    output_dir = output_dir.resolve()

    def call(operation):
        try:
            return operation()
        except TikTokBrowserError as exc:
            raise HTTPException(409, str(exc)) from exc

    def artifact(filename):
        path = (output_dir / str(filename or "")).resolve()
        if path.parent != output_dir or not path.is_file():
            raise HTTPException(404, "Không tìm thấy file đầu ra")
        return path

    def ready_job(job_id):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Không tìm thấy video")
        if job.get("status") != "completed" or not job.get("output_video"):
            raise HTTPException(409, "Video phải xử lý hoàn tất trước khi chuẩn bị đăng")
        return job, artifact(job["output_video"])

    @router.get("/tiktok-browser/status")
    def status():
        return manager.status()

    @router.post("/tiktok-browser/check")
    def check():
        return manager.status(refresh=True)

    @router.post("/tiktok-browser/open")
    def open_studio():
        return call(manager.open_studio)

    @router.post("/tiktok-browser/login")
    def restart_login():
        return call(manager.restart_login)

    @router.delete("/tiktok-browser/session")
    def logout():
        return call(manager.logout)

    @router.post("/tiktok-browser/attempts/{attempt_id}/resolve")
    def resolve(attempt_id: str, reviewed: bool = Form(...)):
        if not reviewed:
            raise HTTPException(422, "Cần kiểm tra bài trong TikTok Studio trước khi kết thúc")
        return call(lambda: manager.resolve(attempt_id))

    @router.get("/jobs/{job_id}/tiktok-draft")
    def draft(job_id: str):
        job, video = ready_job(job_id)
        caption = ""
        if job.get("tiktok_json_file"):
            try:
                content = json.loads(artifact(job["tiktok_json_file"]).read_text(encoding="utf-8"))
                caption = str(content.get("caption") or content.get("title") or "")
            except (HTTPException, OSError, ValueError, AttributeError):
                pass
                
        if not caption:
            import re
            tags = re.findall(r'#[^\s#\.]+', job.get("filename") or "")
            if tags:
                caption = " ".join(tags)
                
        latest = manager.latest_attempt(job_id)
        if latest:
            caption = latest["caption"]
        return {"job_id": job_id, "filename": job.get("filename") or video.name,
                "video_url": f"/download/{quote(video.name)}", "caption": caption, "latest_attempt": latest}

    @router.post("/jobs/{job_id}/tiktok-caption")
    def suggest_caption(job_id: str):
        job, _ = ready_job(job_id)
        if not job.get("translation_file"):
            raise HTTPException(422, "Video chưa có bản dịch để gợi ý caption. Bạn có thể viết caption trực tiếp")
        try:
            payload = json.loads(artifact(job["translation_file"]).read_text(encoding="utf-8"))
            segments = payload.get("segments") if isinstance(payload, dict) else None
            if not isinstance(segments, list):
                raise ValueError("Missing segments")
            usable = [segment for segment in segments if isinstance(segment, dict)
                      and not segment.get("needs_review")
                      and segment.get("translation_status") != "source_fallback"]
            post = LocalExtractiveTikTokProvider().generate(usable, max_summary_chars=280, hashtag_count=5)
        except (OSError, ValueError, TypeError) as exc:
            raise HTTPException(422, "Bản dịch chưa có nội dung đủ tin cậy. Hãy kiểm tra bản dịch hoặc tự chỉnh caption") from exc
        # Preview only: never overwrite the user's caption or a prepared post.
        return {"caption": post.caption, "generator": post.generator}

    @router.post("/jobs/{job_id}/tiktok-browser/prepare", status_code=202)
    def prepare(job_id: str, caption: str = Form(..., min_length=1, max_length=2200), reviewed: bool = Form(...)):
        if not reviewed:
            raise HTTPException(422, "Hãy duyệt video, nội dung và tài khoản trước khi tải lên")
        _, video = ready_job(job_id)
        return call(lambda: manager.prepare(job_id, video, caption))

    return router
