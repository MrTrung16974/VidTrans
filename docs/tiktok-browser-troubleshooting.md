# Khung TikTok bị chặn hoặc màn hình xám

Trên VPS HTTPS, khung phải mở `/tiktok-browser/vnc_lite.html` qua Nginx cùng website.
Không nhúng trực tiếp `tiktok.com` hoặc dùng cổng HTTP localhost của máy chủ.
Bản sửa tự chuyển URL localhost cũ sang đường dẫn proxy và đặt đúng đường dẫn WebSocket.

Sau khi đưa mã nguồn mới lên VPS, chạy trong thư mục dự án:

```bash
bash docker-rebuild.sh --production
```

Lệnh cập nhật backend/frontend và chép template Nginx mới vào cấu hình chạy.
Sau đó tải lại trang và bấm **Kiểm tra phiên**. Nếu gặp HTTP 401/403, đăng nhập lại VidTrans.
HTTP 502 cho biết cần kiểm tra container `tiktok-browser` và upstream Nginx.

Template chỉ thay header chống nhúng của dịch vụ noVNC tại `/tiktok-browser/` và
`/douyin-browser/`, cho phép nhúng cùng origin và vẫn yêu cầu phiên quản trị.
Không xóa chính sách CSP toàn trang hoặc mở cổng CDP/VNC ra Internet.

Bản sửa được kiểm thử bằng `node backend/tests/test_browser_frame.mjs`.
Chưa xác nhận nguyên nhân cụ thể trong phiên người dùng nếu chưa đọc được log/response
của request iframe đã xác thực. Nút **Mở rộng** giúp mở cùng phiên trình duyệt ở tab riêng.
