# TEST REPORT — RBAC và customer billing

- Ngày chạy: `2026-10-12`
- Môi trường: local, dữ liệu tổng hợp, không merchant secret.
- Kết quả feature suite: `21 PASS / 0 FAIL`.
- Visual QA: PASS tại `1440x900`, `390x844`; không overflow, không browser console error.
- Full regression: `232` tests được discover; `231 PASS`, `1 ERROR` do module legacy `infrastructure.douyin_qr_auth` không tồn tại.
- Sandbox MoMo/ZaloPay: `BLOCKED`, cần credential merchant và HTTPS callback URL.
- UAT/production payment: `NOT_RUN`.

## Bổ sung chuyển khoản MB — 2026-10-12

- Targeted suite `tests.test_payments tests.test_auth tests.test_frontend_static`: `17 PASS / 0 FAIL`.
- JavaScript syntax: `PASS`.
- Python AST syntax và `git diff --check`: `PASS`.
- VietQR URL và HMAC callback: `PASS` cấp unit.
- Webhook đối soát thật, giao dịch MB thật và kích hoạt plan production: `NOT_RUN`; cần tài khoản nhận thật, secret triển khai và dịch vụ đối soát.

