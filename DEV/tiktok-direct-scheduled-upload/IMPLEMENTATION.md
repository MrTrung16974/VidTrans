# Implementation report

- Feature: `tiktok-direct-scheduled-upload` 0.1
- Ngày: `2026-10-10`

## Thay đổi

- Thêm `SQLiteTikTokPublishStore` với schema additive, idempotency và atomic schedule claim.
- Thêm `TikTokPublishingService` và `TikTokPublishScheduler` cho direct, local schedule, inbox draft và reconciliation.
- Thêm REST API create/get/latest/cancel publish attempt; response loại bỏ path/idempotency nội bộ.
- Publisher giữ nguyên newline của caption và checkpoint `publish_id` ngay sau init.
- Default OAuth scope gồm `video.upload,video.publish`.
- UI review có mode, privacy, lịch, trạng thái, cancel và nút API chính thức; TikTok Studio là fallback.
- Native schedule private chưa được triển khai theo đúng gate của PLAN.

## Verification

- `python3 -m py_compile ...`: PASS.
- Targeted 64 tests: PASS.
- Frontend module syntax: PASS.
- Browser-frame JS tests: PASS.
- Full Python discovery: 203 test thực thi thành công, 1 import error ngoài feature do thiếu module `infrastructure.douyin_qr_auth`.

## Compatibility

- Auto-publish legacy vẫn hoạt động.
- Account OAuth cũ thiếu `video.publish` cần bấm kết nối lại.
- Database migration additive, không xóa bảng/cột cũ.
