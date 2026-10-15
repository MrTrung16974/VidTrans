# Handoff — Source browser login

- Workspace: VidTrans; cập nhật 2026-10-15.
- Mục tiêu: ba tab Douyin/YouTube/Facebook dùng chung một Chromium/VNC, cookie lọc riêng theo domain và tự cấp cho downloader.
- Phạm vi được người dùng xác nhận bằng yêu cầu “OKi bắt đầu làm”.
- Đọc theo thứ tự: `ANALYSIS.md` → `URD.md` → `DEV/source-browser-login/DESIGN.md` → `IMPLEMENTATION.md` → `TEST_REPORT.md`.
- Code và automated tests hoàn tất; segmented tabs đã được chỉnh CSS và bump cả cache key CSS/JavaScript. Lỗi click tab do cache `app.js` cũ đã được sửa. UAT đăng nhập/tải thật chưa chạy vì cần credential/2FA người dùng.
- 46 Python + 8 JS PASS; compose dev/prod/corporate CA, JavaScript/shell syntax và diff check PASS.
- Runtime local đã recreate `social-browser`, loại bỏ hai browser nguồn cũ; noVNC/API activate và HTTPS YouTube/Facebook PASS.
- Bước tiếp: người dùng đăng nhập từng tab, chạy TC_008–TC_010 và xác nhận tải video thật.
- Mạng hiện tại dùng CA doanh nghiệp; image đã cài CA từ `secrets/mcredit-root-ca.crt`. Nếu môi trường khác vẫn chặn mạng, cấu hình proxy chung `VIDTRANS_SOCIAL_BROWSER_PROXY`.
- Không lưu secret/cookie trong hồ sơ; không log cookie value.
