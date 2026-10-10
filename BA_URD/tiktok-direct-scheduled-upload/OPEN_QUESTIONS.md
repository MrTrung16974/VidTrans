# Open questions

| ID | Nội dung | Ảnh hưởng | Đề xuất mặc định | Trạng thái |
|---|---|---|---|---|
| Q01 | Release đầu có bắt buộc lịch native nằm trên TikTok không? | Scope, rủi ro, thời gian | Release official direct + local schedule trước | OPEN |
| Q02 | Có cho phép dùng API web private/session cookie trên account test? | Legal, security, account safety | Chỉ spike sau phê duyệt; production flag off | OPEN |
| Q03 | Nếu server lên lại sau giờ đăng thì xử lý thế nào? | Business behavior | Trễ <=15 phút đăng; lớn hơn chuyển review | OPEN |
| Q04 | Có cần nhiều tài khoản ngay release đầu? | Schema, UI, credential store | Schema có `account_id`, UI chỉ một active account | OPEN |
| Q05 | Retention video chờ lịch tối đa bao lâu? | Disk/cleanup | Tối thiểu đến lịch + 7 ngày reconciliation | OPEN |
| Q06 | Có cần sửa/hủy lịch native từ VidTrans? | Provider capability | Chưa cam kết đến khi spike xác minh endpoint | OPEN |
| Q07 | App TikTok đã được audit cho `video.publish` và public privacy chưa? | Khả năng đăng public | Preflight; chưa audit thì test `SELF_ONLY` | OPEN |
| Q08 | Native schedule có account business riêng để test không? | Khả năng spike | Bắt buộc account test tách production | OPEN |
