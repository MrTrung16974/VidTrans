# Gợi ý caption TikTok

Video mới dùng bộ tạo local v2: chọn câu mở đầu dễ đọc, thêm tối đa một câu tiếp nối,
loại câu lặp và ưu tiên bản dịch không có cờ cần kiểm tra. Phần nội dung giới hạn
350 ký tự (hoặc giới hạn tóm tắt thấp hơn); hashtag đặt riêng ở cuối, tối đa 5.
Hashtag chỉ được tạo từ cụm chủ đề có trong bản dịch, không cố điền đủ số lượng.

Với video đã tạo: vào **Đăng TikTok**, chọn video → **Gợi ý caption** → xem bản đề xuất
→ **Dùng caption này** hoặc **Giữ bản hiện tại**. Gợi ý dùng bản dịch đã lưu, không cần
render lại. Sau khi áp dụng, đọc lại caption và đánh dấu xác nhận duyệt trước khi tải lên.
Nội dung cũ chỉ đổi trong ô soạn thảo khi bạn bấm áp dụng; file kết quả và bài đã chuẩn bị
không bị ghi đè. Các chỉnh sửa trong ô vẫn cần được gửi khi chuẩn bị bài để lưu vào lượt đăng.

Bộ tạo chạy local, không cần API key. Nó chọn và sắp xếp câu trong bản dịch, chưa phải mô hình
viết lại sáng tạo. Nếu lời dịch còn cứng hoặc sai ý, cần chỉnh bản dịch/caption thủ công.
Video thiếu bản dịch hoặc chỉ có câu cần kiểm tra sẽ không được nút gợi ý tạo nội dung mới.

API: `POST /api/v1/jobs/{job_id}/tiktok-caption` trả `caption` và `generator`;
chỉ dùng video đã hoàn tất, không tải video lên TikTok.
