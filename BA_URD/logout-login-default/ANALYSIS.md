# Phân tích — logout trở về trang đăng nhập

- Trạng thái: `CONFIRMED` theo yêu cầu ngày 23/10/2026.
- AS-IS: `authMode` giữ mode đăng ký/reset; logout chỉ mở auth gate nên form cũ xuất hiện lại.
- TO-BE: mọi lần khóa ứng dụng/logout đều reset auth gate về mode `login` và xóa dữ liệu nhạy cảm ở các form khác.
- Phạm vi: frontend auth state; không đổi API logout/cookie.
- AC01: logout từ mode đăng ký hiển thị form đăng nhập.
- AC02: session hết hạn từ mode reset cũng hiển thị form đăng nhập.
- AC03: mật khẩu/form đăng ký-reset không còn dữ liệu cũ.

