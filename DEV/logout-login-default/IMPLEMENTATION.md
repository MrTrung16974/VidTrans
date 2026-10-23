# Kết quả triển khai

- `lockApplication()` reset bốn auth form và trạng thái password reset.
- Luôn gọi `setAuthMode("login", message)` trước khi hiển thị auth gate.
- Áp dụng cho logout, HTTP 401 và session expiry vì đều dùng chung helper.
- Cập nhật cache key của `app.js`.

Không thay đổi backend session/cookie contract.

