"""The "Cắt clip" feature: cut a finished job's video into several clip jobs.

Each clip is its own job (``job_action = "clip_video"``) in one batch, so queueing,
cancellation, progress events, retry and downloads are shared with every other
job. A clip job only cuts: the translation, voices and subtitles were made once
for the whole source video, so cutting uses no quota.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pipeline.clip_planning import (
    FRAME_VERTICAL_CROP,
    VERTICAL_HEIGHT,
    VERTICAL_WIDTH,
    ClipSpec,
    build_audio_cut_command,
    build_clip_command,
    build_crop_source_command,
    render_srt,
    slice_segments,
    vertical_crop_box,
    vertical_segments,
)

logger = logging.getLogger(__name__)

CLIP_JOB_ACTION = "clip_video"
CLIP_FEATURE = "clip_split"
CLIP_RENDER_TIMEOUT_SECONDS = 3600
DEFAULT_SUBTITLE_FONT_SIZE = 36.0
CROP_NEEDS_CLEAN_SOURCE = (
    "Video nguồn chưa in phụ đề không còn trên máy chủ, nên không cắt dọc 9:16 (crop giữa) được. "
    "Hãy chọn “Dọc 9:16 (nền mờ)” hoặc giữ khung gốc."
)


class ClipSourceError(ValueError):
    """The chosen source job cannot be cut; the message is shown as is (Vietnamese)."""


@dataclass(frozen=True)
class ClipSource:
    job_id: str
    filename: str
    video: Path
    duration: float
    segments: list[dict[str, Any]]
    translation_file: Path | None
    # The source before subtitles were burned in (needed for the 9:16 crop), if still on disk.
    clean_video: Path | None = None
    subtitle_style: dict[str, Any] | None = None
    apply_logo: bool = False


def is_clip_job(job: dict[str, Any]) -> bool:
    return job.get("job_action") == CLIP_JOB_ACTION or job.get("job_type") == CLIP_JOB_ACTION


def read_segments(translation_file: Path | None) -> list[dict[str, Any]]:
    if translation_file is None or not translation_file.is_file():
        return []
    try:
        payload = json.loads(translation_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Unreadable translation file %s", translation_file)
        return []
    segments = payload.get("segments") if isinstance(payload, dict) else None
    return [segment for segment in segments or [] if isinstance(segment, dict)]


def load_clip_source(
    job: dict[str, Any],
    *,
    artifact_path: Callable[[str], Path],
    probe_duration: Callable[[Path], float | None],
) -> ClipSource:
    """The finished video of ``job`` and its subtitle cues, or ClipSourceError."""
    if job.get("status") != "completed":
        raise ClipSourceError("Chỉ cắt được video của tiến trình đã hoàn thành")
    output_name = str(job.get("output_video") or "")
    video = artifact_path(output_name) if output_name else None
    if video is None or not video.is_file():
        raise ClipSourceError("Video thành phẩm của tiến trình này không còn trên máy chủ")
    translation_name = str(job.get("translation_file") or "")
    translation_file = artifact_path(translation_name) if translation_name else None
    duration = probe_duration(video) or float(job.get("source_duration") or 0)
    if duration <= 0:
        raise ClipSourceError("Không đọc được độ dài video thành phẩm")
    stored_request = job.get("resume_request") if isinstance(job.get("resume_request"), dict) else {}
    clean_value = stored_request.get("video_path")
    clean_video = Path(str(clean_value)) if clean_value and Path(str(clean_value)).is_file() else None
    style = stored_request.get("subtitle_style")
    return ClipSource(
        clean_video=clean_video,
        subtitle_style=style if isinstance(style, dict) else None,
        apply_logo=bool(stored_request.get("apply_logo")),
        job_id=str(job["job_id"]),
        filename=str(job.get("filename") or job["job_id"]),
        video=video,
        duration=float(duration),
        segments=read_segments(translation_file),
        translation_file=translation_file,
    )


def clip_job_request(source: ClipSource, clip: ClipSpec, index: int, total: int) -> dict[str, Any]:
    """What the worker needs to cut one clip; absolute paths, re-checked when it runs."""
    return {
        "source_job_id": source.job_id,
        "source_video": str(source.video),
        "translation_file": str(source.translation_file) if source.translation_file else None,
        "clean_video": str(source.clean_video) if source.clean_video else None,
        "subtitle_style": source.subtitle_style,
        "apply_logo": source.apply_logo,
        "clip": {**clip.as_dict(), "index": index, "total": total},
    }


@dataclass
class ClipJobDependencies:
    ffmpeg: str
    update_job: Callable[..., None]
    ensure_active: Callable[[str], None]
    output_dir: Callable[[str], Path]
    artifact_name: Callable[[Path], str]
    utc_now: Callable[[], str]
    # (segments, output_dir, job_id) -> (tiktok json path, tiktok text path) or None.
    write_caption: Callable[[list[dict[str, Any]], Path, str], tuple[Path, Path] | None]
    # The 9:16 crop draws the subtitles again with the pipeline's own renderer.
    work_dir: Callable[[str], Path] | None = None
    video_size: Callable[[Path], tuple[int, int]] | None = None
    # (segments, ass path, style, video_width=, video_height=)
    write_ass: Callable[..., None] | None = None
    # (video, ass, output, audio or None, segments, style, work_dir)
    burn_subtitles: Callable[..., Any] | None = None
    # (job_id, video) stamps the account logo on a finished clip.
    stamp_logo: Callable[[str, Path], None] | None = None


def _run(command: list[str], failure: str, job_id: str) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=CLIP_RENDER_TIMEOUT_SECONDS)
    except subprocess.CalledProcessError as exc:
        logger.warning("Clip ffmpeg step failed for job %s: %s", job_id,
                       (exc.stderr or b"").decode("utf-8", errors="ignore")[-400:])
        raise RuntimeError(failure) from exc


def render_vertical_crop(
    job_id: str, request: dict[str, Any], clip: ClipSpec, segments: list[dict[str, Any]],
    output: Path, deps: ClipJobDependencies,
) -> None:
    """A 9:16 crop of the clean source with the finished video's sound and subtitles drawn again."""
    clean_value = request.get("clean_video")
    clean = Path(str(clean_value)) if clean_value else None
    if clean is None or not clean.is_file():
        raise FileNotFoundError(CROP_NEEDS_CLEAN_SOURCE)
    if not (deps.work_dir and deps.video_size and deps.write_ass and deps.burn_subtitles):
        raise RuntimeError("Máy chủ chưa cấu hình dựng phụ đề cho khung 9:16")
    work = deps.work_dir(job_id) / "vertical-crop"
    work.mkdir(parents=True, exist_ok=True)
    box = vertical_crop_box(*deps.video_size(clean))
    cropped, audio, ass = work / "cropped.mp4", work / "audio.wav", work / "clip.ass"
    _run(build_crop_source_command(deps.ffmpeg, clean, clip, box, cropped), "Không crop được video nguồn", job_id)
    deps.ensure_active(job_id)
    try:
        _run(build_audio_cut_command(deps.ffmpeg, Path(str(request["source_video"])), clip, audio),
             "Không tách được âm thanh lồng tiếng", job_id)
    except RuntimeError:
        audio.unlink(missing_ok=True)  # A video without sound keeps going silent.
    style = dict(request.get("subtitle_style") or {})
    laid_out = vertical_segments(segments, box,
                                 base_font_size=float(style.get("font_size") or DEFAULT_SUBTITLE_FONT_SIZE))
    deps.update_job(job_id, step="rendering-clip-subtitles", progress=0.55,
                    step_detail="Đang vẽ lại phụ đề cho khung dọc 9:16")
    deps.write_ass(laid_out, ass, style, video_width=VERTICAL_WIDTH, video_height=VERTICAL_HEIGHT)
    deps.burn_subtitles(cropped, ass, output, audio if audio.is_file() and audio.stat().st_size else None,
                        laid_out, style, work)


def run_clip_job(job_id: str, request: dict[str, Any], deps: ClipJobDependencies) -> dict[str, Any]:
    """Cut one clip and return the job fields of the finished clip. Cancellation raises from ``ensure_active``."""
    raw_clip = request.get("clip") or {}
    clip = ClipSpec(float(raw_clip["start"]), float(raw_clip["end"]),
                    str(raw_clip.get("title") or ""), str(raw_clip.get("frame") or "original"))
    source_video = Path(str(request.get("source_video") or ""))
    if not source_video.is_file():
        raise FileNotFoundError("Video gốc của clip đã bị xóa khỏi máy chủ; hãy cắt lại từ tiến trình khác")
    deps.ensure_active(job_id)
    deps.update_job(job_id, status="processing", step="cutting-clip", progress=0.2,
                    step_detail=f"Đang cắt “{clip.title}” ({clip.duration:.0f} giây)")
    output_dir = deps.output_dir(job_id)
    output_video = output_dir / f"output_{job_id}.mp4"
    partial = output_dir / f"output_{job_id}.partial.mp4"
    translation_value = request.get("translation_file")
    segments = slice_segments(read_segments(Path(translation_value)) if translation_value else [],
                              clip.start, clip.end)
    try:
        if clip.frame == FRAME_VERTICAL_CROP and segments:
            render_vertical_crop(job_id, request, clip, segments, partial, deps)
        else:
            # Without subtitles the finished video itself can be cropped.
            size = deps.video_size(source_video) if clip.frame == FRAME_VERTICAL_CROP and deps.video_size else None
            _run(build_clip_command(deps.ffmpeg, source_video, clip, partial, source_size=size),
                 "Không cắt được clip từ video gốc", job_id)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        if deps.work_dir is not None:
            shutil.rmtree(deps.work_dir(job_id) / "vertical-crop", ignore_errors=True)
    partial.replace(output_video)
    if clip.frame == FRAME_VERTICAL_CROP and segments and request.get("apply_logo") and deps.stamp_logo:
        # The crop starts from the clean source, which has no logo yet.
        deps.stamp_logo(job_id, output_video)
    deps.ensure_active(job_id)
    deps.update_job(job_id, step="writing-clip-files", progress=0.85, step_detail="Đang tạo phụ đề và caption")

    fields: dict[str, Any] = {"output_video": deps.artifact_name(output_video), "source_duration": clip.duration}
    if segments:
        srt_path = output_dir / f"{job_id}.srt"
        srt_path.write_text(render_srt(segments), encoding="utf-8")
        translation_path = output_dir / f"{job_id}.translation.json"
        translation_path.write_text(json.dumps({
            "version": 1, "clip_of": request.get("source_job_id"),
            "clip": {"start": clip.start, "end": clip.end, "title": clip.title},
            "segments": segments,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        fields.update(subtitle_file=deps.artifact_name(srt_path), translation_file=deps.artifact_name(translation_path))
        try:
            caption = deps.write_caption(segments, output_dir, job_id)
        except Exception:
            logger.warning("Clip caption failed for job %s", job_id, exc_info=True)
            caption = None
        if caption:
            fields.update(tiktok_json_file=deps.artifact_name(caption[0]), tiktok_text_file=deps.artifact_name(caption[1]))
    return fields
