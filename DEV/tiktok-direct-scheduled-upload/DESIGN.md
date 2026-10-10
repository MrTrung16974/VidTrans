# Design — TikTok Publishing V2

- URD: `BA_URD/tiktok-direct-scheduled-upload/URD.md` version 0.1.
- Kiến trúc repo: FastAPI + application services + SQLite repositories + infrastructure adapters.

## 1. Mapping

| UC/BR | API | Application | Repository/integration |
|---|---|---|---|
| UC_01, BR01 | `POST /jobs/{id}/tiktok-publishes` | `TikTokPublishingService.create/run` | `SQLiteTikTokPublishStore`, `TikTokPublisher.publish` |
| UC_02, BR02 | create/cancel/get | `TikTokPublishScheduler` | atomic `claim_due` |
| UC_03, BR03 | create mode `INBOX_DRAFT` | publishing service | `TikTokPublisher.upload_draft` |
| UC_04, BR04 | get/reconcile state | service checkpoint | remote ID callback/status API |

## 2. Domain contract

- Modes: `DIRECT_NOW`, `SCHEDULE`, `INBOX_DRAFT`.
- Statuses: `READY`, `SCHEDULED_LOCAL`, `UPLOADING`, `SUBMITTED`, remote statuses, `NEEDS_RECONCILIATION`, `FAILED`, `CANCELLED`.
- Terminal remote success: `PUBLISH_COMPLETE`; draft delivery: `SEND_TO_USER_INBOX`.
- `idempotency_key` unique toàn store. API không cho client gán status, remote ID hoặc file path.

## 3. Persistence và concurrency

- Bảng additive `tiktok_publish_attempts` trong `work/jobs.sqlite3`.
- Payload JSON chứa caption/options/media metadata; cột riêng chứa status, schedule và idempotency để index/claim.
- `claim_due` chạy trong `BEGIN IMMEDIATE`, đổi status sang `UPLOADING` trước khi trả record.
- `create` bắt unique collision rồi trả record cũ, không tạo side effect thứ hai.

## 4. Remote side effect

- Publisher nhận callback `on_initialized(publish_id)` ngay sau init response.
- Service checkpoint remote ID trước upload chunk.
- Lỗi khi đã có remote ID → `NEEDS_RECONCILIATION`; chưa có ID → `FAILED`.
- Release 0.1 không tự retry attempt lỗi. Người dùng tạo intent mới với key mới sau khi kiểm tra.

## 5. Validation và security

- Job/path được resolve server-side từ job store và output allowlist.
- Caption/permission/schedule được validate server-side.
- Authentication dùng middleware hiện tại; không nhận account ID, token, cookie hoặc path từ form.
- API chỉ trả safe fields trong attempt payload; không lưu upload URL.

## 6. Compatibility

- Luồng auto-publish cũ tiếp tục chạy trong release này để tránh migration cưỡng bức.
- Màn hình review mới dùng V2; TikTok Studio browser vẫn là fallback.
- Default OAuth scopes bổ sung `video.publish`; account đã kết nối trước đó cần reconnect nếu thiếu scope.

## 7. Test strategy

- Store: idempotency, due claim, cancel, concurrent-safe state conditions.
- Service: direct/draft/schedule, caption fidelity, remote checkpoint, ambiguous failure.
- Publisher: newline/hashtag preservation và callback init.
- Routes: validation/object path, duplicate key và status response.
- Frontend static test: controls/endpoints tồn tại.
