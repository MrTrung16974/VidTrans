# Nhật ký triển khai

## Thay đổi

- Thêm manager cookie Chromium cô lập cho YouTube/Facebook.
- Nối snapshot cookie theo platform vào `SocialVideoDownloader`; ưu tiên cookie job → browser snapshot → shared cookie.
- Thêm API status/sync/reload/logout.
- Thêm hai browser sidecar/profile, Nginx authenticated proxy và production URL.
- Chuyển view Douyin thành workspace ba tab, giữ hash/API Douyin cũ.

## Kiểm chứng 2026-10-15

- `python3 -m unittest tests.test_social_browser_auth tests.test_social_video_downloader tests.test_douyin_browser_auth tests.test_frontend_static`: PASS, 44 tests.
- `node tests/test_browser_frame.mjs`: PASS, 8 tests.
- `docker compose config --quiet`: PASS.
- Production compose với hostname tổng hợp: PASS.
- `python3 -m py_compile ...`: PASS.
- `git diff --check`: PASS.

## Giới hạn

- Chưa chạy đăng nhập thật/UAT vì cần người dùng nhập credential/2FA trong browser.
- Test cũ `tests.test_douyin_qr_auth` là BLOCKED bởi module `infrastructure.douyin_qr_auth` không tồn tại trước thay đổi; không thuộc luồng manager Douyin hiện hành.

## Chỉnh UI theo phản hồi 2026-10-15

- Bổ sung final cascade layer có specificity theo `#douyinView` để loại bỏ native button rendering.
- Tab trở thành segmented control ba cột, có màu nhận diện từng nền tảng, focus-visible và responsive mobile.
- Tăng cache key stylesheet lên `20261015-1` để trình duyệt tải CSS mới sau deploy.
- Regression: 7 frontend static tests và 8 browser-frame tests PASS; `git diff --check` PASS.

## Sửa tab không phản hồi 2026-10-15

- Nguyên nhân: HTML vẫn tham chiếu cache key `app.js?v=20261013-1`, nên browser dùng JavaScript cũ dù CSS mới đã tải.
- Đổi cache key thành `app.js?v=20261015-2` và thêm static assertion cho listener chuyển tab.

## Sửa browser trắng 2026-10-15

- Bằng chứng runtime: noVNC/CDP healthy nhưng Chromium log có TLS `net_error -202`; `curl` trong container báo `self-signed certificate in certificate chain`.
- Kết nối Facebook/YouTube vẫn timeout khi chỉ bỏ verify, nên môi trường còn có chặn mạng ngoài lỗi CA.
- Browser Dockerfile nhận optional BuildKit secret `corporate_ca` và cập nhật system trust store; không hạ xác minh TLS.
- Compose có proxy riêng `VIDTRANS_YOUTUBE_PROXY`/`VIDTRANS_FACEBOOK_PROXY`.
- `docker-rebuild.sh --corporate-ca` build CA và nay restart cả YouTube/Facebook browser.
- Kiểm chứng: Dockerfile build PASS; base/corporate/production compose config PASS; shell syntax/diff check PASS.
