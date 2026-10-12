# ANALYSIS — Phân quyền, cổng khách hàng và thanh toán Việt Nam

- Feature: `rbac-customer-billing`
- Phiên bản: `0.1-draft`
- Ngày: `2026-10-12`
- Trạng thái: `IN_REVIEW`
- Nguồn: yêu cầu người dùng ngày 2026-10-12; source hiện tại; tài liệu chính thức MoMo, ZaloPay và VietQR

## 1. Vấn đề và mục tiêu

VidTrans hiện có danh mục tài khoản (`owner/admin/editor/viewer`) và trạng thái gói/thanh toán, nhưng cơ chế đăng nhập vẫn là một tài khoản quản trị duy nhất với JWT scope `admin`. Frontend hiển thị cùng một navigation cho mọi phiên và chưa có cổng tự phục vụ thanh toán cho khách hàng.

Mục tiêu của lát tính năng này:

- Phân quyền thực ở server, không chỉ ẩn menu: `owner`, `admin`, `editor`, `viewer`.
- Header/navigation chuyên nghiệp, phản ánh đúng quyền và thích ứng desktop/tablet/mobile.
- Thêm trang `Thanh toán` cho khách hàng: xem gói, chọn gói, tạo giao dịch và theo dõi trạng thái.
- Thiết kế adapter cho MoMo, ZaloPay và chuyển khoản ngân hàng/VietQR; không lưu secret hoặc dữ liệu thẻ.
- Webhook/IPN xác minh chữ ký, idempotent và chỉ cập nhật đúng đơn hàng/số tiền đã lưu.

## 2. AS-IS — CONFIRMED

- FastAPI + frontend HTML/CSS/JavaScript tĩnh.
- `AuthManager` chỉ phát token cho một username cấu hình, claim cố định `scope=admin`.
- `AdminAccountStore` đã lưu email, display name, role, approval, plan, payment status và provider reference trong SQLite.
- API hiện được bảo vệ ở mức phiên admin toàn cục; chưa có user identity gắn với `admin_accounts`.
- Trang Admin cho phép sửa role/approval/plan/payment status, nhưng chưa có object-level authorization hoặc audit cho thay đổi quyền.
- Chưa có bảng order/payment attempt/webhook event và chưa có provider client.

## 3. TO-BE đề xuất

### Ma trận quyền MVP

| Khả năng | Owner | Admin | Editor | Viewer |
|---|---:|---:|---:|---:|
| Quản trị user/role/approval | Có | Có, trừ owner | Không | Không |
| Quản trị plan/payment thủ công | Có | Có | Không | Không |
| Tạo và chạy workflow | Có | Có | Có | Không |
| Xem tiến trình/kết quả | Có | Có | Có | Có |
| Kết nối/đăng TikTok, Douyin | Có | Có | Có | Không |
| Xem gói và thanh toán của chính mình | Có | Có | Có | Có |

Server lấy user/role từ token đã xác minh và tra account server-side; client không được gửi role để tự cấp quyền. Tài nguyên thanh toán bắt buộc ràng buộc với user hiện tại.

### Luồng thanh toán

1. User mở `Thanh toán`, xem gói và giá được cấu hình server-side.
2. User chọn provider; backend tạo `payment_order` với amount/plan cố định và idempotency key.
3. MoMo/ZaloPay: backend ký request bằng secret môi trường, trả URL/QR của provider; user thanh toán trên trang provider.
4. Chuyển khoản: backend tạo nội dung chuyển khoản duy nhất và QR VietQR. Nếu chưa có API đối soát ngân hàng, trạng thái chỉ là `pending` cho đến khi admin xác nhận; không tự nhận là đã thanh toán.
5. Provider gửi IPN/callback; backend xác minh HMAC/MAC, đối chiếu provider/order/amount, ghi webhook idempotent rồi mới kích hoạt gói.
6. Redirect chỉ dùng để hiển thị; không dùng làm bằng chứng thanh toán.

## 4. Phạm vi đề xuất

### Trong phạm vi

- Auth nhiều user dựa trên account store hiện có; token chứa `sub/user_id`, role và scope phù hợp.
- Guard API theo quyền và guard object thanh toán theo user.
- Header mới: brand, nhóm navigation, active state, billing badge, user menu; mobile navigation có nút mở/đóng.
- Trang Billing responsive: current plan, usage placeholder có nguồn, bảng gói, chọn provider, lịch sử đơn.
- `PaymentOrderStore`, state machine `pending → paid/failed/expired/cancelled`, provider adapter, callback verification, idempotency.
- MoMo/ZaloPay sandbox-ready qua cấu hình môi trường; VietQR/chuyển khoản với chế độ xác nhận thủ công rõ ràng.
- Unit test RBAC, cross-user order access, signature, duplicate callback, amount mismatch và state transition.

### Ngoài phạm vi lát này

- Lưu dữ liệu thẻ, token thẻ hoặc thông tin đăng nhập ngân hàng.
- Tự động đối soát chuyển khoản nếu chưa chọn API ngân hàng/đơn vị open-banking.
- Refund/chargeback tự động, hóa đơn điện tử, recurring tokenization.
- Production activation khi chưa có merchant account, callback HTTPS công khai và kiểm thử sandbox/UAT.

## 5. Bảo mật và tính đúng giao dịch

- Secret chỉ qua environment/secret manager; không ghi vào source, DB, log hoặc tài liệu.
- Callback MoMo/ZaloPay phải kiểm tra chữ ký và dùng constant-time comparison.
- Unique constraint theo `(provider, provider_order_id)` và webhook/event id để chống xử lý trùng.
- Không tin `amount`, `plan`, `user_id`, `role` từ browser; backend lấy từ catalog/order/account.
- Redirect success không nâng cấp gói; chỉ callback/IPN hợp lệ hoặc admin confirmation có audit mới được phép.
- Không log raw callback có PII; mask provider transaction reference khi cần.

## 6. Phụ thuộc và nguồn chính thức

- MoMo Payment Notification: IPN server-to-server, bắt buộc xác minh signature và đối chiếu order/amount; phản hồi trong giới hạn provider.
  - https://developers.momo.vn/v3/docs/payment/api/result-handling/notification/
- ZaloPay Callback/Secure transmission: callback `data` + `mac`, HMAC-SHA256 với callback key/key2.
  - https://docs.zalopay.vn/docs/specs/callback-api/
  - https://docs.zalopay.vn/docs/developer-tools/security/secure-data-transmission/
- VietQR Generate API yêu cầu client ID/API key và thông tin tài khoản nhận tiền.
  - https://www.vietqr.io/generate/

## 7. OPEN — cần người dùng xác nhận

1. Giá và số phút chính xác cho `Free`, `Creator`, `Pro`, `Enterprise`.
2. Merchant nào đã sẵn sàng: MoMo, ZaloPay, VietQR; môi trường sandbox hay production.
3. Chuyển khoản ngân hàng dùng xác nhận thủ công hay có API đối soát từ ngân hàng/đơn vị thứ ba.
4. User đăng nhập bằng email/password riêng ngay trong lát này hay trước mắt dùng tài khoản do admin cấp.

## 8. Tiêu chí nghiệm thu cấp cao

- Không user thường nào gọi được API Admin dù tự sửa DOM/request.
- User A không xem/tạo/xác nhận đơn của user B.
- Navigation đúng quyền ở cả desktop 1440px, tablet 768px và mobile 390px; không tràn ngang.
- Callback sai chữ ký/sai amount/trùng event không kích hoạt gói.
- Khi thiếu merchant config, UI ghi rõ “Chưa khả dụng” và backend fail-closed; không tạo giao dịch giả.

