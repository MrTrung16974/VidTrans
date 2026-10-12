# HANDOFF — RBAC và customer billing

- Workspace: VidTrans
- Ngày cập nhật: `2026-10-12`
- Giai đoạn: `IMPLEMENTED_LOCAL`
- Plan: `0.1 APPROVED` theo xác nhận “Oki bắt đầu làm”.

## Đọc theo thứ tự

1. `BA_URD/rbac-customer-billing/ANALYSIS.md`
2. `BA_URD/rbac-customer-billing/PLAN.md`
3. `BA_URD/rbac-customer-billing/URD.md`
4. `DEV/rbac-customer-billing/DESIGN.md`
5. `DEV/rbac-customer-billing/IMPLEMENTATION.md`
6. `TEST/rbac-customer-billing/TEST_REPORT.md`

## Đã làm

- Multi-user JWT/RBAC và admin guard.
- Account password migration.
- Billing catalog/order/event store.
- Bank manual confirmation, MoMo create/IPN, ZaloPay create/callback.
- Header/menu mới và Billing page responsive.

## Cần làm tiếp trước production

- Cấp merchant sandbox credentials qua secret manager; không ghi vào repo.
- Cấu hình HTTPS public callback, chạy TC_014 và UAT với số tiền test.
- Chốt bảng giá/phút chính thức.
- Thêm audit trail đầy đủ cho admin xác nhận chuyển khoản và UI Admin để đối soát order.
- Chuẩn bị migration/runbook deployment sau sandbox PASS.

## Kiểm chứng

- 21 tests feature/static PASS.
- Full suite có 1 lỗi legacy thiếu module `infrastructure.douyin_qr_auth`.
- Browser QA 1440/390 PASS, không overflow/console error.

