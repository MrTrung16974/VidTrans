# Phân tích — bỏ thông báo gói đã chọn

- Trạng thái: `CONFIRMED` từ yêu cầu trực tiếp ngày 23/10/2026.
- AS-IS: khi chọn gói trả phí, trang hiển thị banner sticky “Đã chọn gói …”.
- TO-BE: banner và toàn bộ code chuyên biệt tạo/style banner được xóa; luồng chọn gói và checkout vẫn hoạt động.
- Phạm vi: HTML placeholder, JavaScript render banner, CSS desktop/mobile.
- Ngoài phạm vi: API billing, dữ liệu gói, chọn card, dropdown thanh toán và tạo order.
- AC01: không còn DOM `billingNotice` hay chuỗi “Đã chọn gói”.
- AC02: chọn gói vẫn cập nhật checkout và cuộn tới khu vực thanh toán.

