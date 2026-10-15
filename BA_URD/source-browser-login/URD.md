# URD — Đăng nhập nguồn mạng xã hội trong trình duyệt tích hợp

## UC_01 — Quản lý phiên đăng nhập nguồn

Actor chọn tab Douyin/YouTube/Facebook, thao tác đăng nhập trực tiếp trong Chromium, sau đó bấm Đồng bộ.

- BR01_01: Mỗi nền tảng dùng profile Chromium và cookie file riêng.
- BR01_02: Backend xác định đăng nhập bằng cookie marker phía server; frontend không được tự khai báo trạng thái.
- BR01_03: Đồng bộ không trả giá trị cookie cho client.
- BR01_04: Logout chỉ xóa cookie/storage của browser đang chọn và cookie file tương ứng.
- AC01_01: Đáp ứng AC01–AC06 trong `ANALYSIS.md`.

## UC_02 — Tự cấp cookie khi tải video

- BR02_01: Cookie tải lên theo job có ưu tiên cao nhất; snapshot browser đứng trước file dùng chung để tránh dùng cookie tĩnh đã cũ.
- BR02_02: Nếu không có cookie job/global, downloader lấy snapshot từ browser đúng platform.
- BR02_03: Snapshot tạm bị xóa sau mọi kết quả thành công/thất bại.
- BR02_04: Không dùng cookie YouTube cho Facebook hoặc ngược lại.

## NFR

- NFR_01 Security: browser CDP chỉ expose trong Docker network; noVNC qua Nginx auth request.
- NFR_02 Compatibility: API Douyin và hash `#douyin` cũ giữ nguyên.
- NFR_03 Operability: trạng thái unavailable phải chỉ rõ container cần kiểm tra.
- NFR_04 Privacy: không log cookie/password/OTP và không commit profile/cookie.
