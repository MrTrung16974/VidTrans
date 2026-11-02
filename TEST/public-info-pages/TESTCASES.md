# Test cases — Trang thông tin công khai (đợt 1)

| ID | AC | Bước | Kỳ vọng | Tự động |
|---|---|---|---|---|
| TC01 | AC01 | Chưa đăng nhập, mở lần lượt `/about`, `/contact`, `/terms`, `/privacy`, `/refund`, `/data-deletion`, `/faq` | 200, đúng tiêu đề, canonical = `<domain>/<slug>` | `test_each_page_is_served_with_absolute_canonical` |
| TC02 | AC01 | Mở `/khong-ton-tai` | 404 | thủ công (nginx) |
| TC03 | AC05 | Mở `/sitemap.xml` | Có `/` + 7 trang | `test_sitemap_lists_every_page` |
| TC04 | AC02 | Footer trên 7 trang và trong app | Đủ 7 link + email | `test_pages_and_app_share_a_footer_linking_every_page` |
| TC05 | AC01 | Build image web | `pages/` được copy, location nginx khớp `INFO_PAGES` | `test_static_web_container_serves_the_same_pages` |
| TC06 | AC03 (một phần) | Mở auth gate | Có link Điều khoản + Quyền riêng tư, mở tab mới | `test_auth_gate_links_terms_and_privacy` |
| TC07 | — | Xem các trang ở 390px | Không cuộn ngang, bảng cuộn trong khung | thủ công |
| TC08 | — | Đăng nhập, cuộn cuối mỗi view | Footer hiển thị, không bị thanh dock dưới che (≤1024px) | thủ công |

## Đợt 2

| ID | AC | Bước | Kỳ vọng | Tự động |
|---|---|---|---|---|
| TC09 | AC04 | Gửi form /contact hợp lệ khi chưa đăng nhập | 202, lưu `new`, mail tới hộp hỗ trợ với Reply-To khách | `test_message_is_stored_and_mailed_to_support_with_reply_to` |
| TC10 | AC04 | SMTP lỗi | Tin vẫn được lưu | `test_mail_failure_does_not_lose_the_message` |
| TC11 | AC04 | Điền honeypot | 202, không lưu, không mail | `test_honeypot_is_accepted_silently_but_not_stored` |
| TC12 | AC04 | Email/chủ đề/nội dung/SĐT sai | 422 | `test_invalid_input_is_rejected` |
| TC13 | AC04 | Gửi lần thứ 6 trong 1 giờ | 429 + Retry-After; hết cửa sổ thì gửi được | `test_rate_limit_per_client_and_window` |
| TC14 | AC04 | Admin xem/đánh dấu đã xử lý | Badge, lọc, audit; editor bị 403 | `test_admin_lists_and_resolves_with_audit`, `test_non_admin_cannot_read_inbox` |
| TC15 | AC03 | Đăng ký có/không tick, client cũ | lưu phiên bản / 422 / tạo nhưng `terms_outdated` | `TermsConsentApiTests` |
| TC16 | AC08 | Đổi `TERMS_VERSION` | Người dùng cũ bị hỏi lại | `test_bumping_the_version_asks_again` |
| TC17 | AC08 | Đăng nhập tài khoản chưa đồng ý (web) | Hộp thoại modal; Esc không đóng; Đồng ý → biến mất | E2E thủ công/CDP |
