# Implementation — Dọn stack TikTok publishing cũ

## Đã thay đổi

- Xoá `application/tiktok_publishing.py`, `application/tiktok_publish_routes.py`, `infrastructure/tiktok_publish_store.py` và test riêng của stack này.
- Bỏ scheduler/router/store TikTok direct riêng khỏi `main.py`; chỉ còn `SocialPublishScheduler`.
- Chuyển `tiktok.js` sang social publish JSON API, chuẩn hóa mode/status/error/remote ID và tăng cache version `20261019-1`.
- Bỏ `paddle_ocr_enabled`, `_paddle_ocr_enabled()` và `VIDTRANS_ENABLE_PADDLE_OCR`; OCR chỉ còn `VIDTRANS_OCR_BACKEND`.
- Retention chỉ kiểm tra `social_publish_attempts` để bảo vệ video còn lịch đăng.
- Thêm bộ SQL migrate, backup/drop, postcheck và rollback cho `tiktok_publish_attempts`.

## Kiểm chứng 2026-10-19

- Regression ứng dụng: 87/87 PASS.
- PostgreSQL 17 disposable: prepare → migrate → backup/drop → postcheck → rollback đều exit 0.
- Migration fixture gồm terminal, scheduled, remote side effect và upload dở không remote; postcheck đúng bốn trạng thái đích.
- Tìm kiếm runtime không còn import/route/config legacy; tham chiếu bảng cũ chỉ còn trong test destructive và artifact DEPLOY.
- Container PostgreSQL test `vidtrans-cleanup-pg` đã bị xoá sau kiểm thử; dữ liệu này là fixture tạm, không có phương án phục hồi và không chứa dữ liệu người dùng.
