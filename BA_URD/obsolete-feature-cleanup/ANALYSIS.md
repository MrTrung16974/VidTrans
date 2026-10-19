# Phân tích — Dọn tính năng và bảng DB không còn sử dụng

## Mục tiêu

Giảm code/runtime trùng lặp sau các thay đổi OCR–ASR, social publishing và PostgreSQL; chỉ xoá code/table khi không còn reader, writer, route, scheduler hoặc dữ liệu cần giữ.

## Hiện trạng có bằng chứng

### CONFIRMED — có thể dọn không đổi nghiệp vụ

- `AppSettings.paddle_ocr_enabled` không được runtime đọc; backend mới dùng `ocr_backend`.
- `_paddle_ocr_enabled()` và biến cũ `VIDTRANS_ENABLE_PADDLE_OCR` chỉ còn cho tương thích/test; cấu hình chuẩn là `VIDTRANS_OCR_BACKEND`.
- Một số migration legacy chạy mỗi startup, nhưng chưa có marker/version chứng minh mọi môi trường đã migrate xong nên chưa được xoá ngay.

### CONFIRMED — chưa được drop

- `tiktok_publish_attempts` vẫn có reader/writer qua `TikTokPublishStore`, scheduler, route `/api/v1/.../tiktok-publishes` và `frontend/js/tiktok.js`.
- `social_publish_attempts` đang phục vụ stack publish đa nền tảng.
- `attempts` trong `tiktok_browser.py` là state của browser-assisted publish, không trùng contract với hai bảng trên.
- `jobs`, `batches`, `admin_accounts`, `quota_job_usage`, `admin_audit_events`, `workflow_events`, `payment_orders`, `payment_events`, `user_platform_oauth_apps` đều có reader/writer hiện hành.

## Hướng TO-BE đề xuất

1. Xoá compatibility OCR đã chết và chuyển toàn bộ test/harness sang `VIDTRANS_OCR_BACKEND`.
2. Hợp nhất TikTok direct/scheduled publish vào stack social chung.
3. Migrate toàn bộ lịch sử/attempt TikTok còn cần giữ sang `social_publish_attempts` theo idempotency key ổn định.
4. Chỉ sau postcheck mới xoá router/service/store/scheduler TikTok cũ và drop `tiktok_publish_attempts`.
5. Giữ `attempts` của TikTok browser cho tới khi browser-assisted flow cũng được thay thế bằng contract khác.

## Rủi ro

- Drop bảng trước khi đổi UI/scheduler sẽ làm hỏng trang TikTok và lịch đăng đang chờ.
- Attempt active cần mapping trạng thái chính xác; migration terminal-only hiện tại chưa đủ.
- Database runtime hiện không xuất hiện PostgreSQL service trong `docker compose ps`, nên chưa xác minh row count/schema production và tuyệt đối chưa chạy DROP.

## Acceptance criteria

- Không còn import/route/scheduler/runtime reference tới stack bị xoá.
- Precheck xác nhận số dòng nguồn, active attempt và khóa idempotency.
- Migration bảo toàn số bản ghi cần giữ và không tạo publish trùng.
- Postcheck pass trước `DROP TABLE`; rollback có thể phục hồi schema/data.
- Regression job, TikTok, social publish và retention pass.
