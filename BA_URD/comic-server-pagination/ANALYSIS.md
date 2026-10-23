# Phân tích — phân trang dự án truyện và storyboard

- Trạng thái: `CONFIRMED`
- Actor: người dùng sở hữu dự án; role quản trị được xem theo quy tắc hiện hữu.
- AS-IS: tải danh sách dài làm DOM và payload tăng theo số dự án/cảnh.
- TO-BE: frontend gửi `page` và `limit`; backend truy vấn đúng lát dữ liệu và trả `total`, `page`, `limit`, `offset`, `total_pages`.
- Phạm vi: danh sách dự án truyện và danh sách cảnh storyboard.
- Ngoài phạm vi: đổi schema DB, thay đổi quyền sở hữu, deploy.
- Tiêu chí nghiệm thu: mỗi request chỉ trả tối đa `limit`; chuyển trang phát sinh request backend; trang vượt phạm vi được chuẩn hóa; job không phải truyện không xuất hiện.
- Bảo mật: danh tính và phạm vi owner lấy từ account phía server, không nhận từ query client.

