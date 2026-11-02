# Implementation — Trang thông tin công khai (đợt 1)

## Thay đổi

- Mới: `web/pages/{about,contact,terms,privacy,refund,data-deletion,faq}.html`, `web/assets/legal.css`.
- `web/index.html`: nạp `legal.css`; footer chung sau `</main>`; dòng “Khi đăng ký hoặc dùng thử, bạn đồng ý…” trong auth gate.
- `web/docker/default.conf.template`: location cho 7 URL; `web/docker/40-vidtrans-seo.sh`: sitemap; `web/Dockerfile` + `web/.dockerignore`: copy `pages/`.
- `backend/application/seo_routes.py`: `INFO_PAGES`, route từng trang, sitemap.
- `backend/tests/test_seo_routes.py`: `InfoPageTests`.

## Kiểm chứng 02/11/2026

- `python3 -m pytest tests/test_seo_routes.py tests/test_frontend_static.py tests/test_douyin_browser_auth.py` → 29 passed.
- Build image `web/` và chạy nginx: 7 URL trả 200, URL lạ 404, canonical tuyệt đối, sitemap 8 `<loc>`.
- Chụp headless Chrome: `/privacy` desktop 1280px, `/contact` và `/terms` ở 390px — không tràn ngang; auth gate hiển thị dòng đồng ý.
- Chưa kiểm tra bằng mắt footer bên trong SPA sau đăng nhập (cần backend chạy).
- `tests/test_billing_reconciliation.py` không chạy được ở máy local (thiếu `cryptography`) và image `vidtrans-vidtrans` cũ (thiếu `socketio`) — không liên quan thay đổi này.

## Đợt 2 — kiểm chứng 02/11/2026

- Test mới: `tests/test_contact_routes.py` (8), `tests/test_terms_consent.py` (8), thêm test `Reply-To` trong `test_password_reset.py`.
- Toàn bộ backend (Docker `vidtrans-vidtrans` + `python-socketio`): 985 test, 8 lỗi. 1 lỗi do contract → đã chạy `scripts/export_openapi.py` (chỉ thêm 4 operation). 7 lỗi còn lại (`test_frontend_static` ×2, `test_idempotency.BrandingIdempotencyApiTests` ×4, `test_animation_video` ×1) **có sẵn trên HEAD** — đã chạy lại trên worktree sạch để xác nhận.
- E2E bằng Chrome headless + CDP, backend thật với SQLite tạm:
  - /contact (390px): gửi form → thông báo thành công; form rỗng bị chặn; tin xuất hiện trong Quản trị › Liên hệ, badge 1; “Đánh dấu đã xử lý” → danh sách và badge về 0.
  - Đăng nhập owner chưa đồng ý → hộp thoại hiện, nút khoá tới khi tick; đồng ý → `terms_outdated=false`, không hiện lại.
  - Đăng ký: form không hợp lệ khi chưa tick; tick → tài khoản mới có `terms_version` hiện hành, không bị hỏi lại.
- Sửa kèm: email đặt lại mật khẩu ghi sai số hỗ trợ `095510400` → `0965 510 400`.
