# PLAN — Phân quyền, customer billing và professional navigation

- Feature: `rbac-customer-billing`
- Version: `0.1`
- Ngày: `2026-10-12`
- Trạng thái: `APPROVED`
- Approval record: `Version 0.1 | Người dùng | 2026-10-12 | "Oki bắt đầu làm"; chấp thuận phạm vi mặc định trong plan`

## 1. Kết quả mong muốn

Một lát tính năng chạy được end-to-end trong local/test: user đăng nhập bằng account riêng, server áp quyền, header/menu thay đổi theo role, khách hàng mở trang Thanh toán, tạo đơn qua provider được bật và nhận trạng thái từ callback đã xác minh. Provider chưa có credential phải bị khóa rõ ràng, không mô phỏng thành công.

## 2. Kế hoạch thực hiện

| Bước | Đầu vào | Việc làm | File đầu ra | Cách kiểm chứng | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| BA-1 | ANALYSIS 0.1 + quyết định giá/provider | Viết UC/BR/AC, ma trận quyền, payment state model | `URD.md`, `OPEN_QUESTIONS.md`, `TRACEABILITY.md` | Review consistency/security | Người dùng duyệt plan | NOT_STARTED |
| DEV-1 | URD đã review | Thiết kế auth migration, RBAC policy, schema order/event, provider adapters | `DEV/rbac-customer-billing/DESIGN.md`, `CONFIG_CONTRACT.md` | Design + rollback review | Quyết định login/provider | NOT_STARTED |
| DEV-2 | Design đã review | Mở rộng account credential an toàn, token claims, permission guard, API `/me` | backend source + unit test | Auth/RBAC/cross-user tests | DEV-1 | NOT_STARTED |
| DEV-3 | RBAC foundation | Payment store/service, MoMo/ZaloPay/VietQR adapters, callback/idempotency | backend source + unit test | Signature/duplicate/mismatch tests | Sandbox contract | NOT_STARTED |
| DEV-4 | API ổn định | Header/navigation mới, page Billing và role-aware UI | frontend source | Visual QA 1440/768/390 + API test | DEV-2/3 | NOT_STARTED |
| TEST-1 | Build local | Viết/runs testcase functional, security, provider failure, regression | `TEST/.../TESTCASES.md`, `TEST_REPORT.md` | Bằng chứng PASS/FAIL/BLOCKED | Merchant sandbox cho E2E | NOT_STARTED |
| DEPLOY-1 | Test report | Migration/precheck/rollback, runbook callback HTTPS và secret contract | `deploy/rbac-customer-billing/` | Rehearsal local/staging | Hạ tầng callback | NOT_STARTED |

## 3. Thứ tự lát triển khai

1. **RBAC nền tảng:** account credential + `/me` + role policy + deny-by-default.
2. **Navigation/UI shell:** header desktop, mobile drawer, user menu, link hiển thị theo quyền.
3. **Billing core:** catalog server-side, order store/state machine/history/ownership.
4. **Bank/VietQR:** QR và nội dung duy nhất; pending + admin confirmation có audit.
5. **MoMo/ZaloPay:** adapter, redirect URL, IPN/callback signature, idempotency.
6. **Hardening:** rate limit, expiry/reconciliation, negative security tests, docs triển khai.

## 4. Definition of Done

- Role được kiểm tra ở backend trên từng endpoint nhạy cảm; frontend chỉ là lớp trình bày.
- Account legacy owner vẫn đăng nhập được hoặc có migration/rollback rõ ràng.
- Payment amount/plan do server quyết định; order và callback idempotent.
- MoMo/ZaloPay callback sai signature hoặc sai order/amount bị từ chối và không đổi subscription.
- Chuyển khoản không có đối soát tự động không bao giờ tự chuyển `paid`.
- Header và Billing đạt responsive/accessibility cơ bản; keyboard focus và touch target tối thiểu phù hợp.
- Unit/regression tests chạy xanh; sandbox E2E được ghi PASS hoặc BLOCKED trung thực.

## 5. Cấu hình dự kiến — chỉ tên, không chứa secret

- `VIDTRANS_PAYMENT_ENABLED`
- `VIDTRANS_PUBLIC_BASE_URL`
- `VIDTRANS_MOMO_PARTNER_CODE`, `VIDTRANS_MOMO_ACCESS_KEY`, `VIDTRANS_MOMO_SECRET_KEY`, `VIDTRANS_MOMO_ENDPOINT`
- `VIDTRANS_ZALOPAY_APP_ID`, `VIDTRANS_ZALOPAY_KEY1`, `VIDTRANS_ZALOPAY_KEY2`, `VIDTRANS_ZALOPAY_ENDPOINT`
- `VIDTRANS_VIETQR_CLIENT_ID`, `VIDTRANS_VIETQR_API_KEY`, `VIDTRANS_BANK_BIN`, `VIDTRANS_BANK_ACCOUNT_NO`, `VIDTRANS_BANK_ACCOUNT_NAME`

Tất cả secret bắt buộc lấy từ môi trường/secret manager và fail-closed khi thiếu.

## 6. Rủi ro và rollback

| Rủi ro | Kiểm soát | Rollback |
|---|---|---|
| Khóa nhầm owner sau migration auth | Bootstrap owner + migration test | Feature flag quay về legacy owner auth |
| User tự nâng quyền | Role từ server/token + DB lookup + permission tests | Tắt multi-user login, giữ owner |
| Callback giả/trùng | HMAC, amount/order match, unique event | Tắt provider, giữ order để reconcile |
| Merchant chưa sẵn sàng | Provider availability API, UI disabled | Chỉ bật bank manual hoặc billing read-only |
| DB migration lỗi | Additive schema, pre/postcheck, backup | Rollback migration trước activation |

## 7. Tự review plan

- Scope: tách rõ UI, auth/RBAC và payment core; không coi ẩn menu là phân quyền.
- Security: identity từ token đã xác minh; object ownership; secret contract; HMAC/idempotency; fail-closed.
- Compatibility: giữ bootstrap owner và thiết kế migration additive.
- Testability: mọi provider qua adapter/mock; sandbox E2E tách khỏi unit test.
- Gap cần review: bảng giá, login model, provider/merchant hiện có và mức tự động hóa chuyển khoản.

> Điểm review: chỉ bắt đầu URD/design/code sau khi người dùng duyệt plan 0.1 và trả lời các quyết định chặn ở ANALYSIS mục 7.

