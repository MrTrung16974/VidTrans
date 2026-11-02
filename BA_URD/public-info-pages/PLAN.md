# Kế hoạch

- Feature: `public-info-pages`
- Trạng thái: đợt 1–2 `IMPLEMENTED_LOCAL` (02/11/2026), đợt 3 `TODO`
- Tài liệu liên quan: `ANALYSIS.md` (phạm vi P1–P8, AC01–AC08, câu hỏi mở).

## Hướng kỹ thuật

- **Trang tĩnh riêng, không nhét vào SPA.** Mỗi trang là file HTML trong `web/pages/` (`about.html`, `contact.html`, `terms.html`, `privacy.html`, `refund.html`, `data-deletion.html`, `cookies.html`, `faq.html`), dùng chung `web/assets/styles.css` + một `web/assets/legal.css` nhỏ. Lý do: crawler/app reviewer đọc được không cần JS, không phải qua auth gate, URL sạch.
- **Nội dung viết thẳng trong HTML tĩnh** (bỏ phương án Markdown + build step vì web không có pipeline build). Header/footer lặp ở mỗi trang; test bảo đảm footer đồng nhất.
- **Routing:** nginx `default.conf.template` + `frontend_static.py` map `/about` → `pages/about.html`, v.v. (`try_files`); gateway production cũng phải cho qua.
- **Footer chung:** partial HTML chèn vào `index.html` và các trang tĩnh; trong SPA footer nằm ngoài `<main>` nên hiện ở mọi view và trong auth gate.
- **Mobile:** mở trang web bằng `url_launcher` (in-app browser), không dựng lại nội dung bằng Flutter → một nguồn sự thật.

## Các bước

| Bước | Việc làm | File chính | Kiểm chứng |
|---|---|---|---|
| 0 | Chốt câu hỏi mở (pháp nhân, liên hệ, hoàn tiền, xoá TK) | `ANALYSIS.md` | Chủ SP xác nhận |
| 1 | Soạn bản nháp nội dung P1–P8 theo dữ liệu thực tế hệ thống xử lý (token MXH, cookie nguồn, video, AI provider, retention) | `web/content/legal/*.md` | Review nội dung + pháp lý |
| 2 | Template trang tĩnh + `legal.css`, header rút gọn (logo + "Mở ứng dụng"), mục lục, "Cập nhật lần cuối" | `web/pages/*.html`, `web/assets/legal.css` | Hiển thị đúng desktop/mobile, dark mode |
| 3 | Route sạch `/about`…`/faq` ở nginx web + backend static + gateway | `web/docker/default.conf.template`, `backend/infrastructure/frontend_static.py`, `deploy/` | `curl -I` 200 khi chưa đăng nhập (AC01) |
| 4 | SEO: title/description/canonical/OG mỗi trang; thêm vào sitemap; robots cho phép | `backend/application/seo_routes.py`, `web/docker/40-vidtrans-seo.sh` | AC05, test sitemap |
| 5 | Footer chung trong SPA + auth gate + trang tĩnh | `web/index.html`, `web/assets/styles.css` | AC02 |
| 6 | Form Liên hệ: API `POST /api/contact` (không cần auth, rate-limit theo IP, honeypot, validate), lưu bảng `contact_messages`, gửi mail qua `mailer.py`, tab "Liên hệ" trong Quản trị (đánh dấu đã xử lý) | `backend/application/contact_routes.py` (mới), `backend/infrastructure/database.py`, `web/js/app.js` | AC04, test API |
| 7 | Đồng ý điều khoản: checkbox ở đăng ký + guest trial; cột `terms_version`, `terms_accepted_at` trong `admin_accounts`; API `/api/auth/me` trả `terms_outdated`; modal chấp nhận lại | `backend/infrastructure/admin_accounts.py`, `auth.py`, `web/js/app.js` | AC03, AC08 |
| 8 | Xoá tài khoản tự phục vụ: nút trong "Tài khoản", xác nhận mật khẩu, trạng thái chờ xoá N ngày → job dọn dữ liệu (video, token MXH, cookie trình duyệt, branding); trang P6 mô tả quy trình | `backend/...account routes`, `job_scheduler.py`, `web/js/app.js` | AC07, test xoá sạch dữ liệu |
| 9 | Mobile: mục "Thông tin & hỗ trợ" trong Hồ sơ, link pháp lý ở Login, checkbox đồng ý khi đăng ký, nút xoá tài khoản; URL lấy từ `env.dart` | `mobile/lib/features/profile/...`, `mobile/lib/features/authentication/...`, `mobile/lib/core/config/env.dart` | AC06, AC07, widget test |
| 10 | Cập nhật URL Privacy/Terms/Data-deletion trong cấu hình app TikTok, Meta, Google OAuth | Console các nền tảng | Kiểm tra thủ công |
| 11 | Tài liệu DEV/TEST theo quy ước repo | `DEV/public-info-pages/{DESIGN,IMPLEMENTATION}.md`, `TEST/public-info-pages/{TESTCASES,TEST_REPORT}.md` | Review |

## Thứ tự giao hàng đề xuất

1. **Đợt 1 — XONG local:** bước 0–5, toàn bộ nội dung P1–P8 (P2 chỉ thông tin tĩnh, chưa có form), dòng đồng ý thụ động ở auth gate.
2. **Đợt 2 — XONG local:** bước 6–7 (form Liên hệ + tab Quản trị › Liên hệ, checkbox đồng ý + `terms_version`, hộp thoại đồng ý lại). Mobile chưa gửi `accept_terms` → tài khoản tạo từ app được hỏi đồng ý khi đăng nhập web; checkbox trên app làm ở đợt 3.
3. **Đợt 3:** bước 8–9 (xoá tài khoản tự phục vụ, mobile), bước 10.

## Rủi ro

- Nội dung pháp lý sai thực tế xử lý dữ liệu (vd. không nêu việc gửi audio/văn bản tới nhà cung cấp AI) → bị từ chối app review hoặc rủi ro pháp lý. Đối chiếu bước 1 với code thật.
- Form liên hệ công khai bị spam → bắt buộc rate-limit + honeypot ngay từ đầu.
- Xoá tài khoản phải xoá cả token MXH và cookie trình duyệt nguồn, không chỉ bản ghi user.
