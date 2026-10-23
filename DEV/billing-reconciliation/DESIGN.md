# Thiết kế

- `payment_orders.customer_reported_at TEXT` (migration `ALTER TABLE` idempotent như `admin_accounts`). Trạng thái vẫn theo URD: `pending → paid | cancelled …`; "chờ đối soát" = `pending` + `customer_reported_at`.
- `PaymentOrderStore.report_transfer(order_id, user_id)`: chỉ đơn của user, provider `bank`, còn `pending`; giữ timestamp đầu tiên.
- `PaymentOrderStore.list_orders(status, awaiting_review, limit, offset)`: JOIN `admin_accounts` lấy email; đơn đã báo xếp trước.
- API:
  - `POST /api/v1/billing/orders/{id}/confirm` → `report_transfer`, không đổi plan.
  - `POST /api/v1/billing/orders/{id}/cancel` → chỉ đơn của chính user.
  - `GET /api/v1/admin/billing/orders` (owner/admin, audit `admin.billing.orders.list`).
  - `POST /api/v1/admin/billing/orders/{id}/reject` (owner/admin, form `reason` ≤200, audit `billing.order_reject`).
  - `POST /api/v1/admin/billing/orders/{id}/confirm` giữ nguyên, dùng `activate_paid_order`.
- UI admin tái dùng layout `.admin-jobs-head/.admin-jobs-row` (7 cột), không thêm CSS.
