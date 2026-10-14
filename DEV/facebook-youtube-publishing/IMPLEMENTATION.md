# Kết quả triển khai — Facebook và YouTube publishing

- Ngày: `2026-10-14`
- Thêm `FACEBOOK`, `YOUTUBE` vào domain platform.
- Thêm OAuth account store với state dùng một lần/TTL, token file 0600.
- Thêm Facebook Page Reels adapter: account discovery, start/upload/finish, checkpoint/reconcile.
- Thêm YouTube adapter: OAuth offline, channel discovery, multipart upload, privacy/title,
  refresh token và processing reconciliation.
- Thêm auth routes dùng chung, đăng ký hai adapter vào application registry.
- Publishing Hub có connection card, account selector, direct/local schedule, consent và
  trạng thái riêng cho Facebook/YouTube.

## Verification

- `PYTHONPATH=backend python3 -m unittest backend.tests.test_facebook_youtube_publishers backend.tests.test_social_publishing backend.tests.test_frontend_static`: 17 tests, PASS.
- JS module syntax check cho `social-extra.js`, `tiktok.js`, `app.js`: PASS.
- `git diff --check`: PASS.
- Lần chạy kèm Instagram: 19/20 pass; một test có sẵn fail vì macOS trả
  `/private/var/...` thay cho `/var/...`, không liên quan feature.
- Chưa smoke test Meta/Google thật vì workspace không có credential test.
- OAuth callback Facebook/YouTube đã được thêm vào public callback allowlist; route test xác
  nhận response account công khai không chứa token.

## Giới hạn

Adapter hiện theo convention publisher sẵn có và nạp video vào memory trước HTTP request.
Với file rất lớn cần nâng request boundary sang streaming/resumable ở hardening tiếp theo.

