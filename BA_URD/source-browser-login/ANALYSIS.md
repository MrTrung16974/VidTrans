# Phân tích — Đăng nhập nguồn YouTube/Facebook trong trình duyệt tích hợp

## Trạng thái

- Phiên bản: 1.0
- Nguồn yêu cầu: người dùng yêu cầu đặt trực tiếp vào màn hình trình duyệt Douyin hiện có và chia tab như khu vực đăng mạng xã hội.
- Phạm vi đã xác nhận: triển khai đầy đủ UI, API, browser sidecar, tự đồng bộ cookie cho bộ tải video.

## Vấn đề và mục tiêu

**CONFIRMED:** VidTrans chỉ có Chromium tương tác cho Douyin. YouTube/Facebook hiện phải tải `cookies.txt` thủ công hoặc cấu hình file dùng chung; cấu hình này gây gián đoạn và khó vận hành trên VPS.

**TO-BE:** Một workspace “Đăng nhập nguồn” có ba tab Douyin, YouTube và Facebook. Mỗi nền tảng có profile Chromium cô lập, nút đồng bộ/tải lại/đăng xuất, và cookie được backend tự cấp cho `yt-dlp` đúng nền tảng.

## Actor và phạm vi

- Editor/Admin/Owner: mở browser, tự nhập thông tin đăng nhập/2FA, đồng bộ và đăng xuất.
- Viewer: không được mở workspace browser theo rule hiện có.
- Backend: không nhận mật khẩu/OTP; chỉ đọc cookie qua CDP private network, lưu file quyền hạn chế và không trả cookie về frontend/log.
- Ngoài phạm vi: tự động nhập mật khẩu/OTP, vượt CAPTCHA, PO Token YouTube, quản lý nhiều tài khoản nguồn.

## Hệ thống ảnh hưởng

- `backend/infrastructure`: quản lý phiên Chromium/cookie và downloader.
- `backend/main.py`: API và wiring provider.
- `backend/frontend`: workspace tab.
- Docker Compose/Nginx: hai sidecar/profile/proxy path mới.

## Acceptance criteria

- AC01: Có tab Douyin, YouTube, Facebook trong cùng workspace; chuyển tab hiển thị đúng browser/status.
- AC02: Đồng bộ chỉ thành công khi cookie xác thực đặc trưng của nền tảng tồn tại.
- AC03: Tải YouTube/Facebook tự lấy snapshot cookie từ browser tương ứng nếu job không tải cookie riêng.
- AC04: Đăng xuất chỉ xóa profile/cookie của nền tảng đang chọn.
- AC05: Cookie không xuất hiện trong API/log/Git và browser route được bảo vệ bằng auth request hiện có.
- AC06: Luồng Douyin hiện hữu không bị regression.

## Giả định và rủi ro

- **ASSUMPTION:** Chấp nhận chạy thêm hai Chromium sidecar độc lập để cô lập phiên và tránh lẫn cookie.
- YouTube có thể yêu cầu PO Token hoặc giới hạn tài khoản; cookie không bảo đảm vượt mọi cơ chế chống bot.
- Ba Chromium tăng RAM; runbook cần nêu rõ theo dõi tài nguyên.
