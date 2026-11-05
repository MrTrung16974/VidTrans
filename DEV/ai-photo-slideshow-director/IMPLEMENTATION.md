# Implementation — AI Photo Slideshow Director

## Đã thực hiện

- Planner AI/heuristic stateless, schema + allowlist validator, exact image-set validation, duration normalization/repeat và music ID validation.
- Endpoint `POST /api/v1/jobs/{job_id}/photo-post/ai-plan` với ownership/state/entitlement check và timeout 120 giây.
- `PhotoPostChoice.slideshow_plan`; server validate snapshot lại trước reserve quota/queue.
- Planned renderer: per-slide motion/timing và xfade/fade/cut; manual renderer vẫn là fallback.
- Web: AI plan card, giữ thứ tự, summary/style/mood, nghe thử/chọn nhạc, apply/re-analyze.
- App: repository/model/UI tương đương web, nghe thử trực tiếp nhạc AI gợi ý.
- OpenAPI cập nhật thủ công vì runtime export không khả dụng trên host.

## Kiểm chứng 2026-11-05

- `python3 -m unittest tests.test_photo_slideshow_planning tests.test_social_video_downloader.TimedSlideshowTests` tại `backend`: PASS, 11 tests.
- FFmpeg 8.1 runtime với hai ảnh màu tổng hợp, `pan_right` + `slow_zoom_out` + crossfade: PASS, tạo MP4 13.163 bytes.
- `node --input-type=module --check < web/js/app.js`: PASS.
- `flutter analyze lib/features/jobs/data/jobs_repository.dart lib/features/jobs/presentation/screens/job_detail_screen.dart`: PASS, no issues.
- `python3 -m json.tool contracts/openapi.json`: PASS.
- `git diff --check`: PASS.
- `tests.test_photo_post`: NOT_RUN; Python hệ thống thiếu `cryptography`, `uv` runtime thiếu `fastapi`, Docker daemon không chạy.
- Browser/widget và render end-to-end qua `compose_slide` + audio: NOT_RUN.

## Giới hạn

- MVP chưa có cache server, BPM/onset analysis, metrics persistence hoặc feature-flag plumbing riêng.
- Transition `cut` dùng xfade cực ngắn 1 ms để giữ một graph thống nhất.
- Cần chạy integration suite trong image backend đầy đủ dependency trước rollout.

