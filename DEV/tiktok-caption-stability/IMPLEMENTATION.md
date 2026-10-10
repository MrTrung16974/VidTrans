# Kết quả sửa lỗi caption TikTok bị ghi đè

## Thay đổi

- `backend/infrastructure/tiktok_browser.py`: lấy lại editor ở mỗi lần điền, kiểm tra caption qua hai nhịp render và điền lại tối đa ba lần nếu TikTok khôi phục tên file.
- `backend/tests/test_tiktok_browser.py`: thêm regression test mô phỏng tên file `output_205deacd` ghi đè ở lần kiểm tra thứ hai.
- `backend/application/tiktok_browser_routes.py`, `backend/frontend/index.html`, `backend/frontend/js/tiktok.js`: thêm hành động **Đăng bài ngay** sau khi người dùng xác nhận đã kiểm tra nội dung.
- Backend chỉ click Post một lần khi attempt ở `awaiting_review`; không retry khi kết quả không chắc chắn và chuyển về kiểm tra thủ công nếu service khởi động lại giữa thao tác.

## Kiểm chứng

- Ngày chạy: 2026-10-10.
- Lệnh: `PYTHONPATH=backend python3 -m unittest backend.tests.test_tiktok_browser backend.tests.test_tiktok_summary backend.tests.test_tiktok_publisher`
- Kết quả: `PASS`, 46 test, 0 lỗi.
- JavaScript: `node --input-type=module --check` cho `tiktok.js` và `app.js`: `PASS`.
- Dòng log `TikTok preparation stopped: RuntimeError` là dữ liệu của test kiểm tra lỗi không xác định; suite kết thúc `OK`.

## Giới hạn

- Chưa chạy smoke test với TikTok Studio thật trong phiên này; cần rebuild dịch vụ trên VPS rồi chuẩn bị và đăng một video thử để xác nhận selector Post với giao diện TikTok hiện tại.
