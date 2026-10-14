# Runbook

1. Cấp biến theo `CONFIG_CONTRACT.md`; đăng ký đúng hai callback OAuth.
2. Rebuild/restart ứng dụng; kiểm tra `/api/v1/social-platforms` có Facebook/YouTube.
3. Kết nối Page/channel test, đăng video tổng hợp ngắn ở chế độ private/unpublished phù hợp.
4. Xác nhận upload → processing → published, refresh/restart không tạo bản sao.
5. Thử local schedule và cancel trước claim.
6. Chỉ bật tài khoản production sau Meta App Review/Google verification hoặc audit cần thiết.

Rollback: gỡ hai publisher khỏi registry và ẩn UI; không xóa attempt/store. Token có thể ngắt
từ UI rồi thu hồi thêm ở Meta/Google. Dừng rollout nếu callback sai, token lộ, duplicate hoặc
trạng thái remote không reconcile được.

