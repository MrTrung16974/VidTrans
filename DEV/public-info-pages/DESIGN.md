# Design — Trang thông tin công khai (đợt 1)

## Quyết định

- 7 trang HTML tĩnh trong `web/pages/<slug>.html`: `about`, `contact`, `terms`, `privacy` (gồm mục cookie), `refund`, `data-deletion`, `faq`. Không qua SPA nên không cần JS, không bị auth gate chặn, crawler và reviewer của TikTok/Meta/Google đọc được.
- URL gọn `/<slug>` phục vụ ở cả hai đường:
  - container web (nginx): `location ~ ^/(about|…|faq)$` → `try_files /pages/$1.html`, `sub_filter` điền `__VIDTRANS_PUBLIC_URL__` cho canonical/OG;
  - backend (chạy all-in-one): `INFO_PAGES` trong `backend/application/seo_routes.py` đăng ký route, thay placeholder, cache theo mtime.
- Sitemap (cả `40-vidtrans-seo.sh` và `seo_routes.py`) liệt kê đủ 7 trang.
- `web/assets/legal.css` dùng lại token màu của `styles.css`; được nạp cả trong SPA cho footer `.site-footer.app-footer` và dòng `.auth-legal`.
- Nội dung viết trực tiếp trong HTML (không build step). Header/footer lặp ở mỗi trang; khi sửa footer phải sửa đủ 8 file (7 trang + `index.html`) — test chặn lệch.

## Nguồn sự thật cho nội dung chính sách

| Khẳng định trong chính sách | Căn cứ trong code |
|---|---|
| Video xoá sau 30 ngày, audit 365 ngày | `VIDTRANS_JOB_RETENTION_DAYS=30`, `VIDTRANS_AUDIT_RETENTION_DAYS=365` (`backend/main.py`) |
| Mật khẩu băm | PBKDF2 (`infrastructure/auth.py`) |
| Token MXH mã hoá | `infrastructure/credential_cipher.py` |
| Cookie HttpOnly, “Ghi nhớ đăng nhập” | `set_cookie(httponly=True, samesite="strict")` |
| ASR chạy nội bộ | faster-whisper (`pipeline/asr.py`) |
| Bên thứ ba AI | Gemini, Pollinations (`image_providers.py`, `llm_providers.py`), Edge TTS, ElevenLabs |
| Thanh toán chờ đối soát, admin kích hoạt | `BA_URD/billing-reconciliation` |

Khi thêm nhà cung cấp/thay retention phải cập nhật `/privacy` và tăng phiên bản.

## Ngoài phạm vi đợt 1

Form liên hệ + API, checkbox đồng ý lưu `terms_version`, xoá tài khoản tự phục vụ, màn hình mobile — xem `BA_URD/public-info-pages/PLAN.md`.

# Đợt 2 — Form liên hệ và đồng ý điều khoản

## Form liên hệ

- `POST /api/v1/contact` (công khai, nằm trong `PUBLIC_AUTH_PATHS`): `name`, `email`, `phone?`, `topic`, `message` (10–4000 ký tự), `website` là honeypot (có giá trị → trả 202 nhưng không lưu).
- Giới hạn 5 tin/giờ/IP (`auth_client_id`, cần `VIDTRANS_TRUST_PROXY_HEADERS=1` sau nginx — production đã bật).
- Lưu vào bảng `contact_messages` **trước**, rồi gửi mail nền tới `VIDTRANS_SUPPORT_EMAIL` (mặc định mr.trung16974@gmail.com) với `Reply-To` = email khách. SMTP lỗi/chưa cấu hình không làm mất tin.
- Không lưu IP (đúng với chính sách quyền riêng tư).
- Quản trị › Liên hệ: `GET /api/v1/admin/contact-messages?status=new|resolved|`, `POST …/{id}/resolve` (ghi audit `admin.contact_message.resolve`). Badge đếm tin mới.
- Code: `backend/infrastructure/contact_store.py`, `backend/application/contact_routes.py`, `web/js/contact-form.js`.

## Đồng ý điều khoản

- `backend/domain/legal.py: TERMS_VERSION = "2026-11-02"`. **Khi sửa /terms hoặc /privacy đáng kể: đổi giá trị này** → mọi tài khoản bị hỏi lại.
- `admin_accounts.terms_version`, `terms_accepted_at` (migration thêm cột). `public_admin_account` trả thêm `terms_outdated`.
- `/auth/register`, `/auth/guest` nhận `accept_terms`: `true` → lưu phiên bản; `false` → 422; **thiếu** (client cũ như app mobile hiện tại) → vẫn tạo, `terms_outdated=true`.
- Web: checkbox bắt buộc ở form đăng ký; nút Dùng thử có dòng “Bấm Dùng thử nghĩa là đồng ý…” và gửi `accept_terms=true`.
- `POST /api/v1/me/terms/accept` (cả role viewer được gọi; ghi audit `account.terms_accept`). Sau đăng nhập, nếu `terms_outdated` → `<dialog id="termsDialog">` modal, không đóng bằng Esc, chỉ có Đồng ý hoặc Đăng xuất.
