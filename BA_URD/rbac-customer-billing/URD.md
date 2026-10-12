# URD — Phân quyền và thanh toán khách hàng

- Feature: `rbac-customer-billing`
- Version: `0.1`
- Trạng thái: `DRAFT — plan đã duyệt, chờ review nghiệp vụ sau implementation local`

## I. Phạm vi

Tạo RBAC server-side cho `owner/admin/editor/viewer`, navigation theo quyền và cổng thanh toán tự phục vụ. Giá MVP cấu hình server-side; mặc định `Creator 199.000đ`, `Pro 499.000đ`; Enterprise liên hệ. Merchant provider chỉ khả dụng khi đủ cấu hình.

## II. Actor và quyền

Ma trận quyền trong `ANALYSIS.md` là nguồn chuẩn cho phiên bản này. `owner/admin` có quyền quản trị tài khoản; mọi role đã phê duyệt được xem billing của chính mình; `editor` được thao tác workflow/publish; `viewer` chỉ xem.

## III. Use case

### UC_01 — Đăng nhập theo tài khoản

- BR01_01: user phải tồn tại, có password hash hợp lệ và `approval_status=approved`.
- BR01_02: token chứa `user_id`, `role`, `scope`; server xác minh chữ ký và tra lại account cho endpoint nhạy cảm.
- AC01_01: tài khoản sai/chưa duyệt/tạm khóa nhận 401/403 và không có session.

### UC_02 — Quản trị quyền

- BR02_01: chỉ owner/admin truy cập API `/admin/*`.
- BR02_02: admin không hạ quyền hoặc khóa owner bootstrap.
- BR02_03: role `viewer` chỉ được đọc dữ liệu, ngoại trừ tự tạo đơn thanh toán và đăng xuất; mọi mutation khác bị backend từ chối.
- AC02_01: editor/viewer tự gọi API admin nhận 403 dù sửa request từ browser.

### UC_03 — Tạo đơn thanh toán

- BR03_01: plan và amount lấy từ catalog server, không nhận amount từ client.
- BR03_02: đơn gắn với `user_id` từ token; user chỉ xem đơn của mình.
- BR03_03: provider thiếu config phải fail-closed.
- AC03_01: tạo đơn hợp lệ trả trạng thái `pending` và hướng dẫn/URL thanh toán tương ứng provider.

### UC_04 — Xử lý callback

- BR04_01: callback phải đúng HMAC/MAC, order/provider/amount và idempotent.
- BR04_02: redirect không được kích hoạt gói.
- AC04_01: callback giả, sai amount hoặc trùng không tạo side effect thanh toán lần hai.

## IV. Trạng thái

`pending → paid | failed | expired | cancelled`. Chỉ callback provider hợp lệ hoặc xác nhận chuyển khoản của owner/admin được chuyển `pending → paid`.

## V. NFR

- NFR_01: không lưu/log merchant secret hoặc dữ liệu thẻ.
- NFR_02: không tràn ngang tại viewport 390/768/1440px.
- NFR_03: touch target chính tối thiểu xấp xỉ 44px; focus-visible rõ.
- NFR_04: callback duplicate không làm đổi subscription lặp.

