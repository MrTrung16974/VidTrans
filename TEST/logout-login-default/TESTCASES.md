# Test cases

| ID | Trường hợp | Kết quả mong đợi | Trạng thái |
|---|---|---|---|
| TC_001 | Mở đăng ký rồi logout | Hiện form đăng nhập, form đăng ký được reset | PASS (static regression) |
| TC_002 | Đang reset mật khẩu rồi nhận 401 | Hiện form đăng nhập, reset state được xóa | PASS (code path review) |
| TC_003 | Đăng nhập lại sau logout | Login hoạt động bình thường | NOT_RUN (UI/UAT) |

