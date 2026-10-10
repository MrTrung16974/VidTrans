# Gợi ý caption TikTok

Video mới dùng bộ tạo local v4: chọn câu mở đầu dễ đọc, thêm tối đa một câu tiếp nối,
loại câu lặp, ưu tiên bản dịch không có cờ cần kiểm tra và thêm một câu hỏi tương tác
theo nhóm chủ đề. Phần nội dung và CTA giới hạn 350 ký tự (hoặc giới hạn tóm tắt
thấp hơn); hashtag đặt riêng ở cuối, tối đa 5.

Hashtag được trộn theo thứ tự: cụm chủ đề có trong bản dịch, nhóm chủ đề liên quan,
rồi tối đa một tag khám phá `#xuhuong` nếu còn quota. Hệ thống không tự thêm `#fyp`,
`#viral` hoặc tag khám phá khi không nhận diện được chủ đề. Đây là chiến lược local
ổn định, không phải dữ liệu xu hướng realtime từ TikTok.

Artifact JSON lưu thêm `relevance_hashtags` và `discovery_hashtags`. Nhóm relevance
gồm tag chủ đề hoặc cụm 2–4 từ được xếp hạng từ toàn bộ bản dịch tin cậy; nhóm discovery
hiện chỉ có `#xuhuong` khi nhận diện được chủ đề. Cách tách hai nhóm được tham khảo từ
Captionaize trong GitHub topic `captioning-videos`, nhưng VidTrans tiếp tục xử lý local
từ transcript thay vì upload video sang một dịch vụ AI khác.

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
