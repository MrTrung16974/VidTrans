# DESIGN — RBAC và customer billing

- Plan/URD: `0.1`, plan được duyệt ngày 2026-10-12.
- Kiến trúc: giữ modular monolith FastAPI + SQLite + frontend tĩnh.

## 1. Backend

- `infrastructure/auth.py`: JWT hỗ trợ claims `user_id`, `role`, `scope`; giữ tương thích owner legacy.
- `infrastructure/admin_accounts.py`: migration additive `password_hash`; lookup/auth account; password không bao giờ xuất qua API.
- `infrastructure/payments.py`: catalog, order store, provider config/adapters và state transition.
- `main.py`: middleware xác thực; helper permission; `/api/v1/me`, `/billing/*`, callback public có signature; guard `/admin/*`.
- Chuyển khoản MB: server tạo VietQR chứa amount/nội dung của order; webhook đối soát ký HMAC mới được phép đánh dấu paid. Tài khoản nhận và secret chỉ lấy từ environment.

## 2. Frontend

- Header thành app navigation có menu toggle, billing link và user menu.
- `billingView` có current plan, plan catalog, provider selector, transfer/payment state và order history.
- `applyPermissions()` ẩn route không được phép nhưng không thay thế backend authorization.

## 3. Data

- `admin_accounts.password_hash` nullable để migration không phá dữ liệu cũ.
- `payment_orders`: order id, user id, provider, plan, amount/currency, state, provider refs, timestamps.
- `payment_events`: unique provider/event id, raw digest thay vì raw payload để chống duplicate mà không lưu PII dư thừa.

## 4. Security

- Identity/role lấy từ JWT đã xác minh và account DB.
- Amount/plan từ catalog server-side.
- HMAC compare bằng `hmac.compare_digest`; callback idempotent.
- Callback ngân hàng xác minh chữ ký trên raw body, đối chiếu provider/amount và transaction ID trước khi kích hoạt gói.
- Provider secrets chỉ từ environment. Không đọc hoặc sửa file cấu hình chứa secret.

## 5. Test strategy

- Auth claims/backward compatibility; account password/approval.
- RBAC admin endpoint; cross-user billing access.
- Order amount server-side; state transition; duplicate callback.
- Frontend static regression và JavaScript syntax; visual QA 1440/768/390.

