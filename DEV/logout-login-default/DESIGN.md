# Thiết kế

`lockApplication()` là điểm hội tụ cho logout, HTTP 401 và phiên hết hạn. Hàm này sẽ:

1. reset các form đăng ký/password-reset;
2. xóa password đăng nhập và trạng thái reset email;
3. gọi `setAuthMode("login", message)`;
4. giữ nguyên trạng thái lỗi/thông tin theo tham số `isError`.

Backend logout và cookie HttpOnly không thay đổi.
