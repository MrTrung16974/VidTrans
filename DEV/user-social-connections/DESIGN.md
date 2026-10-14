# Thiết kế kết nối mạng xã hội theo từng người dùng

## Mục tiêu

VidTrans thương mại không dùng một access token chung trong `.env`. Mỗi người dùng VidTrans tự cấp quyền cho tài khoản mạng xã hội của mình; backend luôn lấy `user_id` từ JWT đã xác minh và chỉ đọc kết nối thuộc user đó.

`client_id` và `client_secret` của ứng dụng VidTrans vẫn là cấu hình cấp hệ thống. Đây là danh tính OAuth của sản phẩm, không phải thông tin đăng nhập của khách hàng. Production nên đưa chúng vào secret manager; `.env` chỉ là cơ chế triển khai hiện tại. Access token, refresh token và metadata của tài khoản khách hàng được lưu theo user trong DB.

## Kết luận nghiên cứu OAuth

- Dùng Authorization Code flow trên backend. Google yêu cầu offline access nếu hệ thống phải refresh token khi user không hiện diện.
- `state` phải ngẫu nhiên, dùng một lần, hết hạn ngắn và gắn với `user_id` cùng platform. DB chỉ lưu SHA-256 của state.
- Dùng PKCE S256 khi provider hỗ trợ. PKCE verifier được mã hóa và chỉ tồn tại trong phiên OAuth ngắn hạn.
- Redirect URI phải cố định và khớp chính xác. Callback không nhận `user_id` từ query/form của trình duyệt.
- Access token và refresh token phải được mã hóa xác thực trước khi ghi DB. Không trả token về frontend, log, audit hoặc API response.
- Khi provider trả refresh token mới, thay thế atomically; nếu không trả refresh token mới, giữ token cũ. Khi user ngắt kết nối, gọi revoke endpoint của provider nếu có rồi xóa ciphertext cục bộ.

Tham khảo:

- OAuth 2.0 Security Best Current Practice RFC 9700: https://www.rfc-editor.org/rfc/rfc9700.html
- Google OAuth 2.0 cho Web Server: https://developers.google.com/identity/protocols/oauth2/web-server
- TikTok Login Kit Web: https://developers.tiktok.com/docs/en/login-kit-web

## Schema đã triển khai

### social_account_connections

Một record là một tài khoản nền tảng đã kết nối với một user VidTrans.

- Khóa duy nhất: `user_id + platform + provider_account_id`.
- Token nằm trong `access_token_ciphertext` và `refresh_token_ciphertext`.
- `encryption_key_id` hỗ trợ xoay khóa mà không đoán khóa nào đã dùng.
- Scope, hạn token, trạng thái, tên hiển thị và metadata provider được lưu riêng để UI không cần giải mã token.
- Foreign key tới `admin_accounts`; xóa user sẽ xóa các kết nối của user.

### social_oauth_sessions

Lưu giao dịch OAuth đang chờ callback.

- Chỉ lưu hash của `state`.
- Gắn `user_id`, `platform`, hạn dùng và PKCE verifier đã mã hóa.
- `consumed_at` bảo đảm callback chỉ dùng một lần.
- `return_path` chỉ nhận đường dẫn nội bộ để tránh open redirect.

### social_connection_audit_events

Ghi sự kiện `connected`, `refreshed`, `revoked`, `expired` hoặc lỗi vận hành mà không ghi token. Tách `user_id` sở hữu khỏi `actor_user_id` để hỗ trợ thao tác quản trị có kiểm soát trong tương lai.

## Ranh giới bảo mật

Mọi repository method đọc hoặc sửa kết nối đều bắt buộc `user_id`. Route không nhận `user_id` để quyết định quyền; route gọi `current_account(request)` và dùng `account["user_id"]`. Admin muốn hỗ trợ user khác phải dùng endpoint quản trị riêng, có audit và kiểm tra role.

Token cipher dùng Fernet từ package `cryptography`, lấy khóa từ `VIDTRANS_CREDENTIAL_ENCRYPTION_KEY`. Khóa mã hóa không nằm trong cùng DB. Production nên lấy khóa từ KMS hoặc Docker/Kubernetes secret và hỗ trợ nhiều key ID trong giai đoạn rotation.

## API đích

```text
GET    /api/v1/social-connections
POST   /api/v1/social-connections/{platform}/authorize
GET    /api/v1/social-connections/{platform}/callback
DELETE /api/v1/social-connections/{connection_id}
POST   /api/v1/social-connections/{connection_id}/refresh
```

Luồng authorize:

1. Backend lấy `user_id` từ phiên đăng nhập VidTrans.
2. Sinh state và PKCE verifier; lưu phiên OAuth có TTL 10 phút.
3. Redirect user đến provider.
4. Callback hash state, consume atomically và lấy lại `user_id` từ phiên đã lưu.
5. Exchange code, truy vấn profile/account provider, mã hóa token rồi upsert connection.
6. Redirect về Publishing Hub bằng `return_path` nội bộ.

## Thay đổi cần làm tiếp theo

### Giai đoạn 1

- Thêm service dùng `CredentialCipher` và `SocialCredentialStore` cho callback.
- Chuyển Facebook, YouTube, Instagram, Threads và TikTok API từ file JSON sang DB.
- Sửa status, accounts, publish và reconcile để luôn truyền `user_id` hoặc `connection_id`.
- Bổ sung endpoint list, authorize và disconnect theo user.

### Giai đoạn 2

- Thay token tĩnh Pinterest và LinkedIn bằng OAuth của từng user.
- UI Settings có danh sách nền tảng, trạng thái, ngày hết hạn, nút kết nối lại và ngắt kết nối.
- Scheduler lưu thêm `user_id` và `connection_id` trong publish attempt để không thể dùng nhầm credential.

### Giai đoạn 3

- Di chuyển SQLite sang PostgreSQL trước khi chạy nhiều replica.
- Dùng KMS envelope encryption, background refresh có distributed lock, webhook revocation và cảnh báo token sắp hết hạn.
- Migration legacy chỉ gán token cũ cho bootstrap owner sau khi xác nhận; không tự động chia sẻ token legacy cho mọi user.

## Điều kiện phát hành thương mại

- Test không đọc chéo connection giữa hai user.
- OAuth state dùng một lần, hết hạn và bị ràng buộc platform/user.
- API response, log, error và audit không chứa token.
- Publish attempt gắn user và connection; scheduler xác minh quyền sở hữu lại trước khi đăng.
- Có backup, xoay khóa, revoke, xóa dữ liệu người dùng và quy trình xử lý token bị lộ.
