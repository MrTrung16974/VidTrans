# IMPLEMENTATION — RBAC và customer billing

- Ngày: `2026-10-12`
- Trạng thái: `IMPLEMENTED_LOCAL`

## Đã triển khai

- JWT claims `user_id`, `role`, `scope`; giữ tương thích bootstrap owner.
- Account store migration additive `password_hash`, đăng nhập bằng email/password và trạng thái phê duyệt.
- Guard server-side cho toàn bộ `/api/v1/admin/*`; helper `require_roles`.
- Viewer được áp read-only ở middleware, ngoại trừ tạo đơn thanh toán của chính mình và đăng xuất.
- API `/api/v1/me`, billing overview, tạo order và admin xác nhận chuyển khoản.
- Payment order/event persistence, user ownership, amount từ catalog, idempotent paid event.
- MoMo `captureWallet`: HMAC-SHA256, create order, checkout URL và IPN verification.
- ZaloPay create order: HMAC-SHA256, Vietnam-date transaction ID, checkout URL và callback verification.
- Header/navigation mới có mobile menu, user menu và ẩn route theo role.
- Page Billing responsive: current plan, catalog, provider availability, checkout và history.

## Giới hạn đúng trạng thái

- Không gọi sandbox thật vì chưa có merchant credentials/callback HTTPS.
- Chuyển khoản ngân hàng cần admin xác nhận; chưa có API đối soát ngân hàng.
- Chưa có refund/chargeback, recurring payment hoặc hóa đơn điện tử.
- Audit log riêng cho manual bank confirmation chưa được thêm; actor đã được dùng trong idempotency payload nhưng đây chưa phải audit trail vận hành đầy đủ.

## Kiểm chứng

- `node --input-type=module --check < backend/frontend/js/app.js`: PASS.
- `python3 -m py_compile ...`: PASS.
- 21 unit/static tests liên quan: PASS.
- Full suite: 232 tests chạy; 231 pass, 1 error có sẵn do thiếu `infrastructure.douyin_qr_auth`, không thuộc feature.
- Browser QA: 1440px và 390px không horizontal overflow; desktop 4 pricing columns, mobile 1 column; console không có error/warn.

