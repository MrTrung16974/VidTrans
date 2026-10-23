# Kết quả triển khai

- `backend/infrastructure/payments.py`: migration cột, `report_transfer`, `list_orders`.
- `backend/main.py`: `/confirm` chỉ ghi nhận; cancel giới hạn theo user; endpoint admin list/reject.
- `web/js/app.js`: nút "Tôi đã chuyển khoản", nhãn "Chờ đối soát", tab admin đối soát (load/render/xác nhận/từ chối).
- `web/index.html`: subtab + pane "Đối soát", option audit mới, cache key `app.js` → `20261023-billing-reconcile`.
- `contracts/openapi.json`: regenerate.
- `backend/tests/test_billing_reconciliation.py`: 7 test.
