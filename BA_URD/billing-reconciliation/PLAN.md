# Kế hoạch

- Feature: `billing-reconciliation`
- Trạng thái: `IN_REVIEW`

| Bước | Việc làm | Kiểm chứng |
|---|---|---|
| DEV | Store: cột `customer_reported_at`, `report_transfer`, `list_orders` | Unit test store |
| DEV | API: `/confirm` chỉ ghi nhận; `GET /admin/billing/orders`; `POST /admin/billing/orders/{id}/reject` | Test endpoint AC01–AC06 |
| DEV | UI khách: "Tôi đã chuyển khoản", nhãn "Chờ đối soát"; UI admin: tab Đối soát | Static test, JS parse |
| TEST | Full suite, regenerate `contracts/openapi.json` | Không lỗi mới |
