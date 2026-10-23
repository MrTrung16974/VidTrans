# Phân tích — đối soát chuyển khoản

- Trạng thái: `CONFIRMED` từ yêu cầu "làm phần thanh toán tiếp" ngày 23/10/2026, tiếp nối `rbac-customer-billing`.
- AS-IS (lỗi bảo mật): `POST /api/v1/billing/orders/{id}/confirm` cho chính khách hàng đánh dấu đơn `paid` và kích hoạt gói, không cần chuyển tiền. Vi phạm URD `rbac-customer-billing` §IV: chỉ callback hợp lệ hoặc owner/admin được chuyển `pending → paid`.
- AS-IS (thiếu): admin không có màn hình xem đơn chờ đối soát; từ chối đơn không có audit.
- TO-BE:
  - Khách bấm "Tôi đã chuyển khoản" chỉ ghi `customer_reported_at`; đơn vẫn `pending`, gói không đổi.
  - Admin có tab "Đối soát thanh toán": lọc đơn khách đã báo, xác nhận (kích hoạt gói) hoặc từ chối kèm lý do; cả hai ghi audit.
  - Khách chỉ hủy được đơn của chính mình; admin từ chối đơn người khác qua endpoint admin có audit.
- Ngoài phạm vi: merchant credential MoMo/ZaloPay, webhook đối soát MB thật, bảng giá chính thức, hết hạn đơn tự động.
- AC01: khách gọi `/confirm` → đơn `pending`, plan không đổi.
- AC02: khách không báo/hủy được đơn của người khác.
- AC03: admin xem danh sách, đơn đã báo đứng đầu, có email người trả.
- AC04: admin xác nhận → `paid`, plan kích hoạt, audit `billing.order_confirm`.
- AC05: admin từ chối → `cancelled`, audit `billing.order_reject` có lý do.
- AC06: non-admin gọi API đối soát nhận 403.
