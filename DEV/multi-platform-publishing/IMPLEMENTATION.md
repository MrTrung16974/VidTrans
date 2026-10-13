# Implementation report — Multi-platform publishing

- Feature: `multi-platform-publishing`
- Mốc: Phase 0–1
- Ngày: `2026-10-13`

## Đã triển khai

- Domain enums và provider protocol cho platform, mode, status, capability và kết quả.
- `PublisherRegistry` để đăng ký adapter và công bố capability cho UI.
- Bảng SQLite additive `social_publish_attempts` với idempotency theo
  platform/account, atomic due claim, cancel và reconciliation lookup.
- `SocialPublishingService` cho create, validation, upload checkpoint, progress,
  failure classification và reconciliation.
- `SocialPublishScheduler` bền vững qua restart nhờ dữ liệu trong SQLite.
- REST API:
  - `GET /api/v1/social-platforms`
  - `POST/GET /api/v1/jobs/{job_id}/social-publishes`
  - `GET /api/v1/social-publishes/{attempt_id}`
  - `POST /api/v1/social-publishes/{attempt_id}/cancel`
  - `POST /api/v1/social-publishes/{attempt_id}/refresh`
- Response API loại bỏ video path, idempotency key, provider options và secret.
- Scheduler mới được gắn vào startup/shutdown của FastAPI.

## Verification

- Targeted Instagram + core + TikTok parity + frontend static: `39 tests`, PASS.
- Full Python discovery: `264 tests` chạy; feature mới PASS.
- Full suite còn 1 lỗi import có sẵn ngoài feature:
  `infrastructure.douyin_qr_auth` không tồn tại nhưng `test_douyin_qr_auth.py` vẫn import.

## Phase 2 — Instagram Reels

- Thêm OAuth state một lần, code exchange, long-lived token exchange và account discovery.
- Thêm Reels flow: create media container, poll status, publish container và lưu cả
  `remote_media_id`/`remote_post_id`.
- Reconciliation không submit lại khi đã có post ID và không publish container chưa `FINISHED`.
- Thêm signed media URL HMAC-SHA256 có TTL, path allowlist và public endpoint không cần admin JWT.
- Thêm auth/status/connect/callback/disconnect routes cho Instagram.
- Thêm biến môi trường và Docker wiring cho Instagram App, Graph API version, public URL
  và media signing secret.

## Chưa hoàn tất

- Chưa có Meta credentials trong workspace nên chưa chạy được staging smoke test thật.
- Token hiện được bảo vệ bằng quyền file tương tự TikTok; encryption-at-rest sẽ được làm
  trước production rollout đa tenant.

## Phase 3 — UI multi-platform

- Publishing Hub hiển thị Instagram cùng TikTok API và TikTok Studio.
- Kết nối/ngắt kết nối, chọn account và tự khôi phục account đầu tiên sau reload.
- Dùng chung video/caption đã review; Instagram có consent riêng để tránh đăng nhầm.
- Hỗ trợ direct publish, local schedule, `share_to_feed`, cancel và status polling.
- OAuth callback đưa người dùng trở lại Publishing Hub và hiển thị kết quả kết nối.
- JavaScript syntax check và static selector/endpoint tests: PASS.

## Phase 4 — TikTok compatibility migration

- `TikTokSocialPublisher` bọc publisher hiện tại, không viết lại OAuth/chunk upload.
- Direct Post giữ privacy/comment/duet/stitch; draft ánh xạ thành `DRAFT_DELIVERED`.
- Remote status TikTok được chuẩn hóa về `PROCESSING_REMOTE`, `PUBLISHED`, `FAILED`
  hoặc `NEEDS_RECONCILIATION`.
- Registry/API chung công bố cả `INSTAGRAM` và `TIKTOK`.
- Lịch sử TikTok terminal được copy idempotent sang `social_publish_attempts`; payload
  không được phép ghi đè status/remote IDs chuẩn hóa.
- Attempt TikTok chưa terminal không được migrate hoặc claim bởi scheduler mới, tránh
  hai scheduler cùng publish/reconcile.
- Routes, store và UI TikTok cũ tiếp tục hoạt động như compatibility layer.

## Full-suite notes

- Lỗi import `infrastructure.douyin_qr_auth` vẫn là lỗi có sẵn ngoài feature.
- `test_auth.AuthTests.test_rejects_tampered_and_expired_tokens` fail một lần trong full
  discovery nhưng pass ngay khi chạy lại riêng; chưa thấy liên hệ với publishing changes.

