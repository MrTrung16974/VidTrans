# Test report — Trang thông tin công khai (đợt 1), 02/11/2026

| ID | Kết quả |
|---|---|
| TC01, TC03–TC06 | PASS (unit) |
| TC02 | PASS — nginx container trả 404 |
| TC07 | PASS — headless Chrome 390px (`/contact`, `/terms`) |
| TC08 | PASS (xem đợt 2) |

## Đợt 2 — 02/11/2026

| ID | Kết quả |
|---|---|
| TC09–TC16 | PASS (unit, chạy trong Docker) |
| TC17 | PASS — Chrome headless + CDP trên backend thật |
| TC08 (đợt 1) | PASS — footer hiển thị trong app sau đăng nhập, 7 link |
| Hồi quy toàn bộ | 7 lỗi có sẵn trên HEAD, không phát sinh lỗi mới |
