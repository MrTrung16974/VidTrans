# Runbook

## Triển khai

1. Kiểm tra RAM/disk và backup `backend/work` nếu cần.
2. Build/recreate stack để tạo `social-browser`; service này dùng lại volume `douyin-browser-profile`.
3. Production: xác minh `/source-browser/` đi qua Nginx HTTPS/auth.
4. Mở **Đăng nhập nguồn**, đăng nhập từng tab và bấm **Đồng bộ**.
5. Tạo job thử bằng một URL công khai của mỗi nền tảng.

## Smoke/monitor

- API status báo `available=true`.
- noVNC load được, WebSocket không 401/502.
- Sau Sync, `authenticated=true`; API không chứa cookie value.
- Theo dõi RAM của `social-browser`, `tiktok-browser` và log cảnh báo provider.

## Rollback

- Khôi phục code/compose/nginx phiên bản trước và recreate `vidtrans`/`nginx`.
- Giữ `douyin-browser-profile` để phục hồi phiên dùng chung; chỉ xóa khi người vận hành chủ động muốn hủy mọi phiên nguồn.
- Không cần rollback DB.
