from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request

from pipeline.tiktok import LocalExtractiveTikTokProvider
from infrastructure.tiktok_browser import TikTokBrowserError, TikTokBrowserManager
from infrastructure.tiktok_publisher import TikTokPublisher, TikTokPublisherError


def create_tiktok_browser_router(manager: TikTokBrowserManager, jobs, output_dir: Path,
                                 publisher: TikTokPublisher | None = None, owner_resolver=None,
                                 publisher_resolver=None, can_manage_all_resolver=None) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["TikTok Browser"])
    output_dir = output_dir.resolve()
    enforce_ownership = owner_resolver is not None
    owner_resolver = owner_resolver or (lambda _request: "__legacy__")
    can_manage_all_resolver = can_manage_all_resolver or (lambda _request: False)

    def owned_job(request: Request, job_id: str):
        if can_manage_all_resolver(request):
            return jobs.get(job_id)
        return jobs.get_for_user(job_id, owner_resolver(request)) if hasattr(jobs, "get_for_user") else jobs.get(job_id)

    def call(operation):
        try:
            return operation()
        except TikTokBrowserError as exc:
            raise HTTPException(409, str(exc)) from exc

    def artifact(filename):
        path = (output_dir / str(filename or "")).resolve()
        if not path.is_relative_to(output_dir) or not path.is_file():
            raise HTTPException(404, "Không tìm thấy file đầu ra")
        return path

    def ready_job(request: Request, job_id):
        job = owned_job(request, job_id)
        if not job:
            raise HTTPException(404, "Không tìm thấy video")
        if job.get("status") != "completed" or not job.get("output_video"):
            raise HTTPException(409, "Video phải xử lý hoàn tất trước khi chuẩn bị đăng")
        return job, artifact(job["output_video"])

    def scoped_status(request: Request, *, refresh: bool = False):
        result = dict(manager.status(refresh=refresh))
        attempt = result.get("attempt")
        if attempt and not owned_job(request, str(attempt.get("job_id") or "")):
            result["attempt"] = None
        return result

    def owned_active_attempt(request: Request, attempt_id: str):
        if not enforce_ownership:
            return manager.active_attempt()
        attempt = manager.active_attempt()
        if not attempt or str(attempt.get("id")) != attempt_id:
            raise HTTPException(404, "Không tìm thấy lượt chuẩn bị")
        if not owned_job(request, str(attempt.get("job_id") or "")):
            raise HTTPException(404, "Không tìm thấy lượt chuẩn bị")
        return attempt

    @router.get("/tiktok-browser/status")
    def status(request: Request):
        return scoped_status(request)

    @router.post("/tiktok-browser/check")
    def check(request: Request):
        return scoped_status(request, refresh=True)

    @router.post("/tiktok-browser/open")
    def open_studio():
        return call(manager.open_studio)

    @router.post("/tiktok-browser/login")
    def restart_login(method: str = "default"):
        """Navigate to TikTok login page.
        method: default | qr
        """
        return call(lambda: manager.restart_login(method=method))

    @router.delete("/tiktok-browser/session")
    def logout():
        return call(manager.logout)

    @router.post("/tiktok-browser/attempts/{attempt_id}/resolve")
    def resolve(request: Request, attempt_id: str, reviewed: bool = Form(...)):
        if not reviewed:
            raise HTTPException(422, "Cần kiểm tra bài trong TikTok Studio trước khi kết thúc")
        owned_active_attempt(request, attempt_id)
        return call(lambda: manager.resolve(attempt_id))

    @router.post("/tiktok-browser/attempts/{attempt_id}/publish")
    def publish(request: Request, attempt_id: str, reviewed: bool = Form(...)):
        if not reviewed:
            raise HTTPException(422, "Hãy kiểm tra video, caption và quyền riêng tư trước khi đăng")
        owned_active_attempt(request, attempt_id)
        return call(lambda: manager.publish(attempt_id))

    @router.get("/jobs/{job_id}/tiktok-draft")
    def draft(request: Request, job_id: str):
        job, video = ready_job(request, job_id)
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
        comic = job.get("comic") if isinstance(job.get("comic"), dict) else {}
        youtube_title = str(comic.get("youtube_title") or job.get("filename") or video.stem).strip()[:100]
        return {"job_id": job_id, "filename": job.get("filename") or video.name,
                "youtube_title": youtube_title,
                "video_url": f"/api/v1/jobs/{job_id}/artifacts/video", "caption": caption, "latest_attempt": latest}

    @router.post("/jobs/{job_id}/tiktok-caption")
    def suggest_caption(request: Request, job_id: str):
        job, _ = ready_job(request, job_id)
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
    def prepare(request: Request, job_id: str, caption: str = Form(..., min_length=1, max_length=2200), reviewed: bool = Form(...)):
        if not reviewed:
            raise HTTPException(422, "Hãy duyệt video, nội dung và tài khoản trước khi tải lên")
        _, video = ready_job(request, job_id)
        return call(lambda: manager.prepare(job_id, video, caption))

    @router.post("/jobs/{job_id}/tiktok-upload-draft", status_code=202)
    def upload_draft(request: Request, job_id: str, caption: str = Form("", max_length=2200), reviewed: bool = Form(...)):
        active_publisher = publisher_resolver(request) if publisher_resolver is not None else publisher
        if active_publisher is None:
            raise HTTPException(503, "TikTok Upload API chưa được cấu hình")
        if not reviewed:
            raise HTTPException(422, "Hãy duyệt video và caption trước khi gửi sang TikTok")
        _, video = ready_job(request, job_id)
        current = owned_job(request, job_id) or {}
        if current.get("tiktok_publish_status") in {"UPLOADING_DRAFT", "PROCESSING_UPLOAD"}:
            raise HTTPException(409, "Video này đang được gửi sang ứng dụng TikTok")
        auth = active_publisher.connection_status()
        if not auth.get("can_upload_draft"):
            raise HTTPException(409, "Hãy kết nối TikTok và cấp quyền video.upload trước")
        jobs.update(job_id, tiktok_publish_status="UPLOADING_DRAFT", tiktok_publish_error=None,
                    tiktok_publish_title=caption, tiktok_publish_mode="INBOX_DRAFT")

        def run() -> None:
            try:
                result = active_publisher.upload_draft(video)
                jobs.update(job_id, tiktok_publish_id=result["publish_id"],
                            tiktok_publish_status=result["status"],
                            tiktok_publish_detail=result.get("status_payload"),
                            tiktok_publish_mode="INBOX_DRAFT")
                status = result["status"]
                # TikTok processes the upload asynchronously; poll until it reaches the inbox.
                for _ in range(60):
                    if status in {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE", "FAILED"}:
                        break
                    time.sleep(5)
                    try:
                        detail = active_publisher.fetch_status(result["publish_id"])
                    except TikTokPublisherError:
                        continue
                    status = detail.get("status") or status
                    jobs.update(job_id, tiktok_publish_status=status, tiktok_publish_detail=detail,
                                tiktok_publish_error=detail.get("fail_reason") if status == "FAILED" else None)
            except TikTokPublisherError as exc:
                jobs.update(job_id, tiktok_publish_status="FAILED",
                            tiktok_publish_error=str(exc), tiktok_publish_mode="INBOX_DRAFT")

        threading.Thread(target=run, daemon=True, name=f"tiktok-draft-{job_id[:8]}").start()
        return {"job_id": job_id, "status": "UPLOADING_DRAFT",
                "message": "Đang gửi video sang hộp thư TikTok trên điện thoại"}

    return router
