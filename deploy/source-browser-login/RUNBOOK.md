# Runbook

## Triển khai

1. Kiểm tra RAM/disk và backup `backend/work` nếu cần.
2. Build/recreate stack để tạo `youtube-browser`, `facebook-browser` và hai named volumes.
3. Production: xác minh `/youtube-browser/` và `/facebook-browser/` đi qua Nginx HTTPS/auth.
4. Mở **Đăng nhập nguồn**, đăng nhập từng tab và bấm **Đồng bộ**.
5. Tạo job thử bằng một URL công khai của mỗi nền tảng.

## Smoke/monitor

- API status báo `available=true`.
- noVNC load được, WebSocket không 401/502.
- Sau Sync, `authenticated=true`; API không chứa cookie value.
- Theo dõi RAM của ba browser sidecar và log cảnh báo provider.

## Rollback

- Khôi phục code/compose/nginx phiên bản trước và recreate `vidtrans`/`nginx`.
- Hai named volume mới có thể giữ để phục hồi; chỉ xóa khi người vận hành chủ động muốn hủy phiên đăng nhập.
- Không cần rollback DB.
