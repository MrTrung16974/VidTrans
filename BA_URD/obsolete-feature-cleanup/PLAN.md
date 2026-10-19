# Plan — Dọn tính năng và bảng DB không còn sử dụng

## Pha 1 — cleanup chắc chắn an toàn

1. Xoá `paddle_ocr_enabled`, `_paddle_ocr_enabled()` và support `VIDTRANS_ENABLE_PADDLE_OCR`.
2. Đổi manual harness/test sang `VIDTRANS_OCR_BACKEND=off|paddle|tesseract`.
3. Chạy config/OCR/pipeline regression.

## Pha 2 — hợp nhất TikTok publishing

1. Chốt mapping TikTok mode/status/payload sang `SocialPublishingService`.
2. Chuyển `tiktok.js` và các route gọi từ UI sang social publish API.
3. Chuyển auto-publish và scheduler sang store chung; giữ idempotency và tenant authorization.
4. Viết migration data cho cả terminal và active attempts; chạy trên fixture PostgreSQL/SQLite cô lập.
5. Chạy regression TikTok/browser/social/job/tenant.

## Pha 3 — DB cleanup có kiểm soát

1. Precheck schema, row count, active statuses, orphan jobs và checksum/count sau migration.
2. Backup/snapshot theo môi trường triển khai.
3. Vô hiệu hóa writer cũ, deploy code hợp nhất.
4. Postcheck không còn query tới bảng cũ.
5. Drop `tiktok_publish_attempts`; không drop bảng `attempts` của browser.
6. Chuẩn bị rollback tạo lại bảng và restore dữ liệu từ snapshot/migration backup.

## Điểm review bắt buộc

Đây là thay đổi kiến trúc và phá huỷ dữ liệu. Cần người dùng duyệt rõ việc **hợp nhất TikTok direct/scheduled vào social publishing chung và drop duy nhất bảng `tiktok_publish_attempts`** trước khi code pha 2–3 hoặc chạy SQL DROP. Pha 1 có thể thực hiện độc lập sau cùng quyết định đó để bàn giao một lượt nhất quán.
