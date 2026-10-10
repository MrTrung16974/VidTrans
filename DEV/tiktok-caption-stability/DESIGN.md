# Thiết kế sửa lỗi caption TikTok bị ghi đè

## Phạm vi

- Sửa luồng chuẩn bị bài trên TikTok Studio khi caption đã được gửi đúng từ UI/API nhưng editor bị TikTok render lại thành tên file.
- Cho phép người dùng xác nhận thông tin rồi bấm **Đăng bài ngay** từ VidTrans.

## Hiện trạng và nguyên nhân

`backend/infrastructure/tiktok_browser.py` lấy editor trước khi chờ upload hoàn tất, gọi `fill()` một lần và đọc lại ngay. TikTok Studio có thể render lại editor sau thời điểm đó; lần kiểm tra tức thời vẫn đúng nhưng caption sau đó bị giá trị mặc định là tên file ghi đè.

## Thiết kế

- Sau khi upload hoàn tất, truyền `page` vào hàm điền caption để luôn truy vấn lại editor hiện tại.
- Mỗi lần thử: xác nhận chỉ có một editor hiển thị, điền caption, chờ một nhịp render, rồi đọc lại.
- Thử lại tối đa ba lần nếu TikTok thay đổi nội dung; không tải lại video và không bấm Đăng.
- Nếu caption vẫn không ổn định, giữ trạng thái cần kiểm tra với lỗi hiện có.
- Chỉ cho phép gọi API đăng khi attempt hiện tại ở trạng thái `awaiting_review` và người dùng gửi xác nhận `reviewed=true`.
- Backend khóa trình duyệt, kiểm tra đúng trang TikTok và đúng một nút Post/Đăng đang bật rồi click một lần. Không retry click khi kết quả không chắc chắn để tránh đăng trùng.
- Sau click, attempt chuyển sang `publish_submitted`; người dùng vẫn kiểm tra trạng thái cuối trên TikTok rồi kết thúc lượt.

## Kiểm thử

- Regression: caption bình thường vẫn được điền và luồng không bấm Đăng.
- Lỗi tái hiện: TikTok ghi đè lần điền đầu bằng tên file; lần điền tiếp theo phải khôi phục caption mong muốn.
- Đăng bài: từ chối khi chưa review, sai trạng thái, nút Post không duy nhất/không sẵn sàng; click đúng một lần ở happy path.
- Chạy unit test cô lập `backend/tests/test_tiktok_browser.py`.

## Bảo mật và tương thích

- Không thay đổi auth/session, đường dẫn file, contract API hay dữ liệu nhạy cảm.
- Không tự retry upload hoặc click Post nên không tăng nguy cơ tạo bài trùng.
