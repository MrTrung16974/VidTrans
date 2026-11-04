# Handoff — qidian-chapter-import

- Ngày: 04/11/2026
- Phạm vi đã làm: P0, P1 (backend), P2 (web), phần tách tập tự động của P3.
- Đã thêm (P4): adapter Jinjiang `novel_sources/jjwxc.py` (HTTP thường). Fanqie không làm vì font mã hóa.
- Chưa làm: mobile (P3), dịch trước khi không có LLM.

## Thay đổi

| File | Nội dung |
|---|---|
| `backend/infrastructure/novel_sources/base.py` | Model chương/mục lục, phát hiện text bị bảo vệ, tách tập theo ranh giới chương (cắt theo câu khi một chương quá dài) |
| `backend/infrastructure/novel_sources/browser.py` | Mở một page riêng trong Chromium Nguồn qua CDP, mỗi lúc một lượt |
| `backend/infrastructure/novel_sources/qidian.py` | Adapter Qidian: parse link, mục lục, nội dung chương, chờ qua `probe.js` |
| `backend/infrastructure/novel_sources/service.py` | Cache (mục lục 24 giờ; chương theo số chữ), nghỉ 5–12 giây giữa các lần tải, thử lại 3 lần, chạy nền 1 worker, hủy, giữ kết quả 1 giờ |
| `backend/application/novel_import_routes.py` | `GET /api/v1/comic/sources`, `GET …/catalog?url=`, `POST …/imports` (bắt buộc `rights_confirmed`), `GET/DELETE …/imports/{id}`; quyền editor trở lên; sau gói `comic_video` |
| `backend/main.py`, `social_browser_auth.py` | Gắn service/router; tab đăng nhập `qidian` |
| `web/js/novel-import.js`, `web/js/comic.js`, `web/index.html`, `web/assets/styles.css` | Ô "Nhập chương từ link truyện" trong form Truyện mới và Thêm tập; tập đầu điền vào form, các tập sau tự thêm vào series khi tạo |
| `web/js/app.js`, `web/js/browser-frame.js` | Tab Qidian ở trang Nguồn |
| `contracts/openapi.json` | Thêm các route mới |
| `backend/tests/test_novel_import.py` | 14 test: quy tắc, link, service, API |

## Kiểm chứng

- `test_novel_import.py`: 14/14 pass. Toàn bộ backend: 1078 pass. 10 test lỗi có sẵn từ trước do môi trường local thiếu thư viện; lỗi y hệt trên code gốc.
- Contract OpenAPI được xuất lại từ app.
- Adapter chạy với trang thật trên VPS (qua stdin, không deploy): mục lục 591 chương; chương 1 đủ 4.315 ký tự; chương VIP chưa mua nhận ra là chỉ có phần xem trước.
- **Chưa** thử giao diện web trên trình duyệt; chưa deploy.
