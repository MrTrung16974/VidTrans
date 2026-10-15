# Báo cáo kiểm thử — 2026-10-15

- Automated: 54 PASS (46 Python + 8 JavaScript).
- Compose dev/production/corporate-CA, JavaScript/shell syntax và whitespace check: PASS.
- UAT đăng nhập/tải thật: 3 NOT_RUN, cần credential và thao tác 2FA của người dùng.
- Known unrelated blocker: suite QR Douyin cũ import module đã bị thiếu sẵn; test manager Douyin hiện hành PASS.
- CSS refinement regression: 7 frontend static + 8 browser-frame PASS; stylesheet cache key đã đổi.
- Runtime smoke: `social-browser` và `tiktok-browser` healthy; noVNC và API activate YouTube/Facebook PASS.
- Corporate CA runtime: YouTube/Facebook trả HTTP 200 trong `social-browser`; Douyin có kết nối HTTPS nhưng root URL trả HTTP 404 từ upstream.
- Hai browser nguồn cũ đã được Compose loại bỏ; cả ba tab dùng chung cổng noVNC 5201.

Kết quả unit test không thay thế xác nhận UAT hoặc triển khai production.
