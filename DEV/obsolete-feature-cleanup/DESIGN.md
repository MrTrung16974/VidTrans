# Design — Hợp nhất TikTok publishing và dọn compatibility cũ

## Nguồn và phạm vi đã duyệt

- Plan: `BA_URD/obsolete-feature-cleanup/PLAN.md`.
- Người dùng duyệt hợp nhất TikTok direct/scheduled vào social publishing và xoá `tiktok_publish_attempts` sau migration/postcheck.
- Không thay đổi TikTok browser-assisted flow; bảng `attempts` vẫn được giữ.

## Mapping thành phần

| Luồng | Trước | Sau |
|---|---|---|
| TikTok direct/draft/schedule UI | `tiktok_publish_routes` → `TikTokPublishingService` → `TikTokPublishStore` | `social_publish_routes` → `SocialPublishingService` → `SocialPublishStore` |
| Scheduler TikTok | `TikTokPublishScheduler` riêng | `SocialPublishScheduler` duy nhất |
| Dữ liệu attempt | `tiktok_publish_attempts` | `social_publish_attempts`, `platform=TIKTOK` |
| OCR config | `paddle_ocr_enabled` + legacy env | chỉ `ocr_backend` + `VIDTRANS_OCR_BACKEND` |

## Contract UI/API

- `tiktok.js` dùng JSON API `/api/v1/jobs/{job_id}/social-publishes`.
- Mode map: `DIRECT_NOW→DIRECT_NOW`, `SCHEDULE→SCHEDULE_LOCAL`, `INBOX_DRAFT→DRAFT`.
- Refresh/cancel dùng `/api/v1/social-publishes/{attempt_id}`.
- Tenant identity vẫn lấy từ authenticated request; connection TikTok được resolver phía server xác minh.

## Migration DB

- Đọc mọi row của `tiktok_publish_attempts`, join `jobs` để lấy `owner_user_id`.
- Attempt terminal map trực tiếp. Attempt active có remote ID map `NEEDS_RECONCILIATION`; attempt upload dở không remote map `FAILED` để tránh duplicate side effect; lịch chưa đến giữ `SCHEDULED_LOCAL`.
- ID migration ổn định `legacy-tiktok-{attempt_id}` và `INSERT OR IGNORE` để idempotent.
- Precheck/postcheck so sánh tổng nguồn với số row đích theo prefix; chỉ drop khi không còn writer cũ và count đủ.
- SQL drop tách khỏi startup, có backup table/rollback; không tự drop khi application boot.

## Concurrency/idempotency

- Chỉ còn một `SocialPublishScheduler` claim `SCHEDULED_LOCAL`.
- Unique key hiện có `(owner_user_id, platform, account_id, idempotency_key)` giữ chống đăng trùng.
- Migration giữ idempotency key và remote IDs; attempt không chắc side effect được chuyển reconciliation/failed, không tự publish lại.

## Test strategy

- Migration terminal, scheduled, active remote và active không remote; chạy lặp không nhân đôi.
- TikTok social adapter/service modes, tenant ownership, create/list/refresh/cancel.
- Static frontend không còn endpoint TikTok publish cũ.
- Config OCR không còn legacy field/env.
- Regression social publishing, TikTok adapter/browser, job retention.
