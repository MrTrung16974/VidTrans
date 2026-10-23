# Handoff

- Feature: `billing-reconciliation` — `IMPLEMENTED_LOCAL`, UAT chưa chạy.
- Đã vá lỗi khách tự kích hoạt gói qua `/billing/orders/{id}/confirm`; endpoint giữ nguyên path (mobile contract) nhưng chỉ ghi nhận báo chuyển khoản.
- Thêm tab admin "Đối soát thanh toán" với xác nhận/từ chối có audit.
- Cần làm tiếp: UAT trên trình duyệt (desktop/mobile), báo cho client mobile biết `/confirm` không còn kích hoạt gói, webhook MB thật, hết hạn đơn `pending` lâu ngày.
