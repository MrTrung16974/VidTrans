# Phân tích — qidian-chapter-import (P0)

- Ngày: 04/11/2026
- Môi trường: VPS production, Chromium `social-browser` (trình duyệt Nguồn), chưa đăng nhập Qidian.
- Truyện thử: `天理协议` — 海棠灯, book `1045928363`.

## Kết quả P0

| Kiểm tra | Kết quả |
|---|---|
| HTTP thường (`curl`) từ máy local và VPS | HTTP 202, trang 209 B chỉ có `probe.js`. Không dùng được |
| Mở mục lục bằng Chromium thật trên VPS | Vượt được `probe.js`, mất 9,2 giây. **IP VPS không bị chặn** |
| Mục lục | 10 quyển, 591 chương. 80 chương miễn phí, 503 chương VIP bị khóa (icon `em.iconfont` glyph U+E63C), 8 chương nằm trong quyển VIP nhưng đọc được |
| Chương miễn phí `第1章 龙女` | 136 đoạn, 4.315 ký tự (đúng bằng `章节字数` trên mục lục), không có ký tự Private Use Area |
| Chương VIP chưa mua `第80章` | Nhận ra là chỉ có phần xem trước (139–142 ký tự) |
| Cookie khi chưa đăng nhập | `_csrfToken`, `newstatisticUUID`, `fu`, `w_tsfp`, `x-waf-captcha-referer`, cookie thống kê. Không có cookie tài khoản |
| Trang tự tải lại sau `probe.js` | Phải bỏ qua lỗi "Execution context was destroyed" và chờ tiếp (đã xử lý trong adapter) |

## Quyết định kỹ thuật

- **Giấy phép:** `hcoona/three` dùng LGPL-3.0-or-later. Không chép nguyên văn script; adapter VidTrans tự viết lại dựa trên các đặc điểm trang đã quan sát (selector, chuỗi đánh dấu, glyph khóa).
- **Phát hiện text bị bảo vệ:** trên 2% ký tự Private Use Area, hoặc text ngắn hơn 35% số chữ trên mục lục, thì báo lỗi và bỏ qua chương, không giải mã.
- **Tiếng Trung:** prompt chia cảnh đã yêu cầu viết lời kể bằng ngôn ngữ đích (`pipeline/comic_storyboard.py:131`), nên LLM dịch trong bước chia cảnh. Đường heuristic (không có LLM) chưa dịch trước; đây là việc còn lại.
- **Đăng nhập:** tab Qidian ở trang Nguồn coi là đã đăng nhập khi có cookie `ywguid` và `ywkey`. **Chưa xác minh** vì chưa có lần đăng nhập thật. Nhập chương miễn phí không cần đăng nhập.

## Chưa xác minh

- Lấy chương VIP với tài khoản đã mua (tài khoản phụ có thể chưa mua chương nào).
- Chất lượng chia cảnh khi đưa text tiếng Trung với lời kể tiếng Việt.
- Thử giao diện web trên trình duyệt thật.
- Trang voz `page-2`, `page-3`: bị Cloudflare chặn khi đọc tự động.
