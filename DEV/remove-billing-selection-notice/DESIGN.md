# Thiết kế xóa component

Xóa đúng ba lớp của component:

1. `web/index.html`: node `#billingNotice`.
2. `web/js/app.js`: đoạn tạo nội dung và mở banner trong `selectBillingPlan`.
3. `web/assets/styles.css`: style `.billing-notice` ở desktop và responsive.

Không đổi contract backend, state billing, phân quyền hoặc thao tác thanh toán.
