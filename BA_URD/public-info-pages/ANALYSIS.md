# Phân tích — Trang thông tin công khai (About, Liên hệ, Chính sách)

- Trạng thái: `CONFIRMED` 02/11/2026 — đợt 1 `IMPLEMENTED_LOCAL`.
- AS-IS:
  - Web là SPA một file `web/index.html`, điều hướng bằng hash (`#create`, `#jobs`, `#billing`…). Không có footer chung, không có trang Giới thiệu / Liên hệ / Điều khoản / Quyền riêng tư.
  - Auth gate (`#authGate`) cho đăng ký + "Dùng thử ngay" nhưng **không** có ô đồng ý Điều khoản/Chính sách.
  - `seo_routes.py` sitemap chỉ có `/`; mọi URL khác không được index.
  - Mobile (`mobile/lib/app/router.dart`) có `/login`, `/jobs`, `/profile` (+ billing) — không có màn hình pháp lý/hỗ trợ.
  - Hệ thống đã xử lý dữ liệu nhạy cảm cần công bố: email, mật khẩu, token mạng xã hội (TikTok/Facebook/YouTube/Instagram/Threads — mã hoá bằng `credential_cipher`), cookie trình duyệt nguồn (Douyin/YouTube/Facebook), video người dùng tải lên (lưu theo `video_retention_days`), thanh toán gói.
  - TikTok/Meta/Google app review **bắt buộc** URL Privacy Policy, Terms of Service và hướng dẫn xoá dữ liệu công khai, truy cập không cần đăng nhập.
- TO-BE: bộ trang công khai chuẩn của một SaaS, truy cập được khi chưa đăng nhập, có URL cố định (index được), liên kết từ footer web, auth gate và màn hình Hồ sơ trên mobile.

## Phạm vi trang

| # | Trang | URL | Nội dung chính |
|---|---|---|---|
| P1 | Giới thiệu (About) | `/about` | Sứ mệnh, tính năng (dịch video, truyện tranh → video, đăng đa nền tảng), đội ngũ/pháp nhân |
| P2 | Liên hệ (Contact) | `/contact` | Email hỗ trợ, hotline/Zalo, giờ làm việc, form gửi yêu cầu |
| P3 | Điều khoản sử dụng | `/terms` | Tài khoản, nội dung người dùng & bản quyền video nguồn, hành vi cấm, giới hạn trách nhiệm, chấm dứt |
| P4 | Chính sách quyền riêng tư | `/privacy` | Dữ liệu thu thập, mục đích, bên thứ ba (TikTok/Meta/Google, nhà cung cấp AI), lưu trữ & thời hạn, quyền người dùng |
| P5 | Chính sách thanh toán & hoàn tiền | `/refund` | Gói, chu kỳ, phút xử lý, điều kiện hoàn tiền, huỷ gói |
| P6 | Xoá dữ liệu / tài khoản | `/data-deletion` | Cách tự xoá, ngắt kết nối mạng xã hội, gửi yêu cầu xoá (yêu cầu của Meta) |
| P7 | Chính sách cookie | gộp vào `/privacy#cookies` | Cookie phiên, ghi nhớ đăng nhập, localStorage |
| P8 | Câu hỏi thường gặp (FAQ) | `/faq` | Định dạng hỗ trợ, thời gian xử lý, lỗi thường gặp, giới hạn gói |

Ngoài phạm vi đợt này: blog, trang trạng thái hệ thống, đa ngôn ngữ EN (để đợt 2, nhưng thiết kế sẵn cấu trúc).

## Tiêu chí chấp nhận

- AC01: Mỗi URL P1–P8 trả về HTML 200 khi **chưa đăng nhập**, có `<title>`, meta description, canonical đúng.
- AC02: Footer hiển thị trên mọi view web (kể cả auth gate) với link tới P1–P8 và năm bản quyền.
- AC03: Form đăng ký và "Dùng thử ngay" yêu cầu tick "Tôi đồng ý Điều khoản & Chính sách quyền riêng tư"; backend lưu `terms_version` + `terms_accepted_at`.
- AC04: Form Liên hệ gửi được khi chưa đăng nhập, có rate-limit + honeypot, gửi email tới hộp hỗ trợ qua `mailer.py`, admin xem được trong tab Quản trị.
- AC05: `sitemap.xml` liệt kê P1–P8; `robots.txt` không chặn các URL này.
- AC06: Mobile: màn hình Hồ sơ có mục "Giới thiệu · Liên hệ · Điều khoản · Quyền riêng tư · Xoá tài khoản" mở trang tương ứng; màn hình đăng nhập có link Điều khoản/Quyền riêng tư.
- AC07: Người dùng tự yêu cầu xoá tài khoản từ web và mobile (yêu cầu App Store/Google Play).
- AC08: Khi đổi phiên bản điều khoản, người dùng cũ thấy thông báo chấp nhận lại ở lần đăng nhập tiếp theo.

## Quyết định của chủ sản phẩm (02/11/2026)

1. Chưa có công ty → ghi “cá nhân vận hành, chưa thành lập doanh nghiệp”; địa chỉ Thuỵ Hà, Phúc Thịnh, Hà Nội.
2. Liên hệ: mr.trung16974@gmail.com, 0965 510 400 (Zalo). Giờ hỗ trợ tạm đặt T2–T7 8:00–21:00 (chưa được xác nhận).
3. Gói đã đăng ký và thanh toán thì **không hoàn tiền**; chỉ trả lại khi lỗi giao dịch (trừ tiền không kích hoạt, chuyển trùng, chuyển dư).
4. Xoá tài khoản: chờ 30 ngày, được huỷ yêu cầu.
5. Nội dung pháp lý do Claude tự soạn, chủ sản phẩm rà lại.
