# PLAN — User tự kết nối và đăng bài mạng xã hội

- Feature: `self-service-social-connections`
- Phạm vi MVP: TikTok API, Facebook Page, YouTube và Instagram Professional
- Mục tiêu UX: user đăng nhập VidTrans, bấm **Kết nối**, đăng nhập nền tảng và cấp quyền; không nhập App ID, App Secret, access token, callback URL hoặc liên hệ admin.

## Trạng thái triển khai

- Phase 0: đang thực hiện ngoài code — cần đăng ký/duyệt production app tại từng provider.
- Phase 1: hoàn tất nền móng — encrypted DB adapter cho bốn provider, API list/get/revoke connection dùng chung và tenant ownership cho publish attempt.
- Phase 2: hoàn tất MVP — UI YouTube/Facebook/Instagram/TikTok API gửi connection ID; cả bốn có vertical slice OAuth → encrypted connection → publish/reconcile.
- Phase 3: hoàn tất MVP — publish attempt lưu `connection_id`; bốn provider bắt buộc connection thuộc user trước khi đăng.
- Phase 4–5: chưa bắt đầu.

## 1. Kết luận kiến trúc

Không thể loại bỏ ứng dụng OAuth cấp hệ thống: Meta, Google và TikTok bắt buộc sản phẩm VidTrans có App ID/Client ID, secret, redirect URI và quy trình xét duyệt riêng. Tuy nhiên đây là việc của đơn vị vận hành VidTrans khi triển khai/phát hành, không phải việc của từng user.

Sau khi hệ thống được kích hoạt một lần, toàn bộ user sử dụng self-service:

```text
User đăng nhập VidTrans
  -> bấm Kết nối nền tảng
  -> VidTrans tạo OAuth state + PKCE
  -> chuyển tới trang đăng nhập chính thức của nền tảng
  -> user cấp quyền
  -> callback về VidTrans
  -> VidTrans lưu connection theo user_id
  -> user chọn tài khoản/Page/channel và đăng hoặc đặt lịch
```

Không cung cấp form App Secret cho user cuối. Không cho user dán access token thủ công.

## 2. Trải nghiệm người dùng đích

### Chưa kết nối

- Hiển thị nút `Kết nối TikTok`, `Kết nối Facebook`, `Kết nối YouTube`, `Kết nối Instagram`.
- Click mở OAuth trong cùng tab hoặc popup có kiểm soát.
- Sau callback quay lại đúng Social Publishing Hub.
- Hiển thị toast `Đã kết nối <tên tài khoản>`.

### Đã kết nối

- Hiển thị avatar, tên tài khoản và loại tài khoản.
- Nếu provider trả nhiều Page/channel, hiển thị từng lựa chọn như một connection riêng.
- Cho phép `Thêm tài khoản`, `Kết nối lại`, `Ngắt kết nối`.
- Mỗi lần đăng phải chọn connection cụ thể và xác nhận đúng tài khoản.

### Lỗi hoặc hết quyền

- Token hết hạn nhưng refresh được: backend tự refresh, user không bị gián đoạn.
- Refresh thất bại hoặc scope bị thu hồi: hiện nút `Kết nối lại`.
- Không hiển thị “hãy cấu hình env” hoặc yêu cầu liên hệ admin cho lỗi thuộc phiên user.
- Nếu toàn hệ thống chưa kích hoạt provider, ẩn provider bằng feature flag thay vì để user gặp nút chết.

## 3. Mô hình dữ liệu

### `social_account_connections`

Một dòng là một tài khoản mạng xã hội thuộc một user VidTrans:

```text
connection_id
user_id
platform
provider_account_id
display_name
avatar_url
status
scopes_json
access_token_ciphertext
refresh_token_ciphertext
encryption_key_id
expires_at
refresh_expires_at
provider_metadata_json
created_at / updated_at / revoked_at
```

Ràng buộc:

```text
UNIQUE(user_id, platform, provider_account_id)
INDEX(user_id, platform, status)
```

### `social_oauth_sessions`

- Chỉ lưu hash của `state`.
- Gắn `user_id`, `platform`, PKCE verifier mã hóa và thời hạn 10 phút.
- `state` chỉ dùng một lần.
- `return_path` chỉ nhận đường dẫn nội bộ.

### `social_publish_attempts`

Bắt buộc bổ sung và sử dụng:

```text
owner_user_id
connection_id
job_id
platform
status
idempotency_key
remote_media_id
remote_post_id
```

Không dùng `account_id` do browser gửi lên làm ranh giới phân quyền. `connection_id` phải được tra bằng cả `connection_id + owner_user_id`.

## 4. API self-service

```text
GET    /api/v1/social-connections
GET    /api/v1/social-connections/{platform}
POST   /api/v1/social-connections/{platform}/authorize
GET    /api/v1/social-connections/{platform}/callback
POST   /api/v1/social-connections/{connection_id}/refresh
DELETE /api/v1/social-connections/{connection_id}

POST   /api/v1/jobs/{job_id}/social-publishes
GET    /api/v1/jobs/{job_id}/social-publishes
GET    /api/v1/social-publishes/{attempt_id}
POST   /api/v1/social-publishes/{attempt_id}/cancel
POST   /api/v1/social-publishes/{attempt_id}/refresh
```

Request đăng bài chỉ nhận:

```json
{
  "connection_id": "uuid",
  "mode": "DIRECT_NOW",
  "caption": "Nội dung đã duyệt",
  "scheduled_for": null,
  "timezone": "Asia/Bangkok",
  "options": {},
  "reviewed": true,
  "idempotency_key": "client-uuid"
}
```

Backend tự suy ra `user_id`, `platform`, provider account và token. Không nhận token hoặc `user_id` từ client.

## 5. Luồng OAuth chung

1. Xác thực phiên VidTrans và lấy `user_id` từ JWT.
2. Kiểm tra provider đang bật bằng cấu hình hệ thống.
3. Sinh state ngẫu nhiên và PKCE S256 nếu provider hỗ trợ.
4. Lưu OAuth session TTL 10 phút, gắn user và platform.
5. Trả `authorization_url` cho frontend.
6. User đăng nhập trên website chính thức của provider.
7. Callback consume state atomically, không lấy user từ query string.
8. Exchange authorization code ở backend.
9. Lấy danh sách account/Page/channel mà user đã cấp quyền.
10. Mã hóa token và upsert từng connection.
11. Redirect về `/#tiktok?connection=<platform>-success` hoặc đường dẫn nội bộ tương đương.

## 6. Chi tiết bốn nền tảng

### TikTok API

- Login Kit + Content Posting API.
- Scope tối thiểu theo publish mode; kiểm tra `video.publish` trước khi bật Direct Post.
- Lưu mỗi `open_id` thành connection của user.
- Hỗ trợ reconnect khi scope thiếu hoặc token hết hạn.
- TikTok Web/browser automation là luồng riêng; không dùng chung profile giữa nhiều user.

### Facebook Page

- User đăng nhập Facebook một lần.
- Backend gọi `/me/accounts` và tạo một connection cho mỗi Page được chọn/cấp quyền.
- Lưu Page access token mã hóa, không dùng user token trực tiếp khi publish Page Reel.
- UI hiển thị tên Page, không hiển thị ID kỹ thuật nếu có tên.

### YouTube

- Authorization Code flow với offline access và consent phù hợp để nhận refresh token.
- Khám phá channel bằng YouTube Data API.
- Một Google account có thể trả channel phù hợp; mỗi channel là một connection.
- Khi provider không trả refresh token mới, giữ refresh token cũ atomically.

### Instagram

- Chỉ nhận Instagram Professional đủ điều kiện Content Publishing.
- Khám phá account sau OAuth và lưu mỗi account thành connection.
- Video phải có signed HTTPS media URL thời hạn ngắn để Meta tải.
- Thông báo rõ khi account cá nhân không đủ điều kiện và hướng dẫn chuyển Professional; đây là lỗi điều kiện tài khoản, không phải cấu hình env.

## 7. Bảo mật và phân quyền

- Mọi route connection lấy `user_id` từ JWT đã xác minh.
- Mọi query connection bắt buộc có `WHERE connection_id=? AND user_id=?`.
- Job và connection phải cùng owner trước khi tạo publish attempt.
- Scheduler kiểm tra lại ownership và trạng thái connection ngay trước publish.
- Token mã hóa xác thực; khóa mã hóa nằm ngoài SQLite.
- API, log, audit, error và frontend không chứa token/raw provider response.
- Disconnect gọi revoke endpoint nếu provider hỗ trợ rồi xóa ciphertext cục bộ.
- Admin không được đăng thay user bằng connection của user qua route thông thường.
- OAuth callback state dùng một lần, TTL ngắn, ràng buộc platform và user.

## 8. Phân kỳ triển khai

### Phase 0 — Product OAuth readiness

- Đăng ký production app cho Meta, Google và TikTok.
- Đăng ký bốn callback HTTPS trên `vidtrans.cloud`.
- Hoàn tất verification/app review/scopes cần thiết.
- Nạp secret bằng Docker secret/secret manager trong quá trình deploy.
- Provider chưa approved phải bị feature flag ẩn khỏi user production.

Gate: tài khoản test không phải admin hệ thống bấm Connect và tới đúng consent screen.

### Phase 1 — Connection service dùng chung

- Tạo `SocialConnectionService` trên `SocialCredentialStore` và `CredentialCipher`.
- Tạo adapter contract: authorize, exchange, discover accounts, refresh, revoke.
- Thay các token JSON theo thư mục user bằng connection DB mã hóa.
- Giữ migration đọc token cũ cho bootstrap owner một lần; không chia sẻ token legacy.

Gate: hai user kết nối cùng một platform nhưng chỉ thấy connection của mình.

### Phase 2 — API và UI self-service

- Thêm API chung `/social-connections`.
- UI bốn tab dùng cùng một component trạng thái connection.
- Thêm account picker, reconnect, disconnect và callback toast.
- Không render hướng dẫn `.env` cho user.

Gate: toàn bộ connect/reconnect/disconnect thực hiện trên web, không SSH và không sửa file.

### Phase 3 — Publishing gắn connection

- Migration `social_publish_attempts`: `owner_user_id`, `connection_id`.
- Request publish dùng `connection_id`, bỏ `account_id` làm khóa tin cậy.
- Worker giải mã token đúng lúc, không đưa token vào payload hàng đợi.
- Scheduler validate lại connection trước upload.

Gate: User A không thể đăng bằng connection của User B kể cả biết ID.

### Phase 4 — Provider rollout

Thứ tự đề xuất:

1. YouTube: OAuth/refresh rõ ràng, dùng làm vertical slice.
2. Facebook Page: account discovery và Page token.
3. Instagram Professional: signed media + Meta processing.
4. TikTok API: scope/app review và publish capability phức tạp hơn.

Mỗi provider chỉ bật production sau contract test, staging smoke test và app review.

### Phase 5 — Production operations

- Background refresh có distributed lock.
- Webhook revocation khi provider hỗ trợ.
- Cảnh báo connection sắp hết hạn.
- Rate limit theo user/platform/connection.
- Chuyển SQLite sang PostgreSQL trước khi chạy nhiều replica.
- KMS envelope encryption và key rotation.

## 9. Test bắt buộc

- A không list/get/revoke connection của B.
- A không publish job của A bằng connection của B.
- OAuth state sai, hết hạn hoặc dùng lần hai bị từ chối.
- Callback không nhận quyền sở hữu từ `user_id` trên URL.
- Double click Connect/Publish không tạo grant hoặc bài đăng trùng.
- Refresh token rotation giữ token cũ nếu provider không trả token mới.
- Disconnect khi lịch đang chờ khiến worker dừng an toàn.
- Restart giữa upload không tạo bài trùng; remote checkpoint chuyển reconcile.
- Token không xuất hiện trong API, HTML, log, audit hoặc exception.
- Bốn provider có test user A/user B độc lập.

## 10. Thứ tự pull request

1. `social-connection-service-and-adapter-contract`
2. `social-connection-self-service-api-ui`
3. `publish-attempt-connection-ownership`
4. `youtube-per-user-connection`
5. `facebook-page-per-user-connection`
6. `instagram-per-user-connection`
7. `tiktok-api-per-user-connection`
8. `postgres-refresh-worker-hardening`

## 11. Definition of Done

- User đăng ký/đăng nhập VidTrans bình thường.
- Bấm Connect trên từng nền tảng và chỉ thao tác trên consent screen chính thức.
- Callback tự quay lại VidTrans và hiển thị đúng account/Page/channel.
- User không thấy hoặc nhập App ID, secret, token hay callback URL.
- User đăng ngay/đặt lịch bằng connection của chính mình.
- Hai user không đọc chéo token, connection, video hoặc publish attempt.
- Token tự refresh hoặc UI yêu cầu reconnect mà không cần admin can thiệp.
- Provider bị lỗi độc lập; không làm hỏng kết nối của platform khác.
- Audit, revoke, backup và key rotation có runbook production.

