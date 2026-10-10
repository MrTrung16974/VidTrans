# Runbook

## Precheck

- Backup SQLite và kiểm tra dung lượng output đủ giữ video chờ lịch.
- TikTok Developer App đã được cấu hình; account test có scope cần thiết.
- Không thử public post nếu app chưa qua audit; dùng `SELF_ONLY`.

## Smoke test

1. Mở một job completed và xác nhận caption nhiều dòng hiển thị đúng.
2. Gửi draft, kiểm tra trạng thái inbox.
3. Direct Post `SELF_ONLY`, theo dõi đến terminal state.
4. Tạo lịch sau vài phút, restart app, xác nhận lịch được claim một lần.
5. Tạo lịch khác rồi hủy, xác nhận không có remote ID.

## Rollback

- Rollback code nhưng giữ bảng additive.
- Không xóa attempt có `remote_publish_id`.
- Trước rollback, ghi danh sách `SCHEDULED_LOCAL` và `NEEDS_RECONCILIATION` để xử lý thủ công.
- TikTok Studio/browser flow cũ vẫn là fallback.

## Stop conditions

- Có dấu hiệu tạo bài trùng.
- Caption payload khác snapshot đã duyệt.
- 429/anti-spam tăng bất thường.
- Attempt có remote ID bị tự upload lại.
