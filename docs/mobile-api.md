# API cho ứng dụng di động (Flutter)

Web và app iOS/Android dùng chung một API. Hợp đồng nằm ở `contracts/openapi.json`; app Flutter
sinh client Dart từ file này, không dùng chung code với web.

| Môi trường | Địa chỉ API |
| --- | --- |
| Production | `https://<tên miền>/api/v1/...` (cùng tên miền với web) |
| Local, Docker | `http://localhost:5210` (chỉ API) hoặc `http://localhost:5200` (qua container web) |
| Android emulator | `http://10.0.2.2:5210` |
| iOS simulator | `http://127.0.0.1:5210` |

## Sinh client Dart

```bash
# Cập nhật hợp đồng sau mỗi lần đổi API (test ContractTests báo khi bị lệch)
cd backend && python scripts/export_openapi.py

# Sinh client (cần Java hoặc dùng image Docker của openapi-generator)
docker run --rm -v "$PWD:/local" openapiapitools/openapi-generator-cli generate \
  -i /local/contracts/openapi.json -g dart-dio -o /local/mobile/packages/vidtrans_api
```

Nhiều endpoint hiện trả JSON không khai báo model, nên client sinh ra có kiểu `Object`/`Map` cho
những response đó. Khi app cần, bổ sung `response_model` ở backend rồi sinh lại.

Các route cũ `/process-video`, `/convert`, `/status/{job_id}`, `/download/{filename}` đã đánh dấu
`deprecated`. App mới dùng `/api/v1/batches`, `/api/v1/jobs/{job_id}`,
`/api/v1/jobs/{job_id}/artifacts/{kind}`.

## Đăng nhập

App không dùng cookie. Gửi `client=native` khi đăng nhập để nhận refresh token trong JSON:

```http
POST /api/v1/auth/token
Content-Type: application/x-www-form-urlencoded

username=user@example.com&password=...&client=native&remember=true
```

```json
{
  "access_token": "...", "token_type": "bearer", "expires_in": 1800,
  "refresh_token": "...", "refresh_expires_in": 432000,
  "scope": "...", "role": "editor", "remember": true
}
```

- Lưu hai token bằng `flutter_secure_storage` (Keychain/Keystore), không lưu ở SharedPreferences.
- Gọi API với `Authorization: Bearer <access_token>`. Không cần header `X-VidTrans-Request`: header
  đó chỉ chống CSRF cho cookie của web.
- Khi API trả `401`, làm mới token rồi gửi lại request:

```http
POST /api/v1/auth/refresh
Content-Type: application/json

{"refresh_token": "..."}
```

Response trả `access_token` mới. Refresh token giữ nguyên hạn ban đầu, tức là phiên kéo dài tối đa
`VIDTRANS_REFRESH_TOKEN_TTL_DAYS` ngày kể từ lần đăng nhập. Hết hạn, đổi mật khẩu, hoặc tài khoản bị
khoá thì refresh trả `401`/`403`: đưa người dùng về màn hình đăng nhập. Đăng xuất trên app chỉ cần
xoá hai token trong secure storage.

## Tiến trình job theo thời gian thực

WebSocket `wss://<tên miền>/api/v1/jobs/events`, xác thực bằng header (app native gửi được header,
trình duyệt thì không):

```dart
final channel = IOWebSocketChannel.connect(
  Uri.parse('wss://vidtrans.cloud/api/v1/jobs/events'),
  headers: {'Authorization': 'Bearer $accessToken'},
);
```

Token sai thì server từ chối ngay lúc bắt tay (client thấy lỗi handshake HTTP 403 hoặc mã đóng
`4401`). Token hết hạn khi đang kết nối thì server đóng với mã `4401`. Cả hai trường hợp: làm mới
token rồi kết nối lại. Server không đọc gì từ client; app không cần gửi tin nhắn.

### Sự kiện

Mọi tin nhắn là JSON có trường `type`. App bỏ qua `type` không biết.

| `type` | Khi nào | Nội dung |
| --- | --- | --- |
| `ready` | Ngay sau khi kết nối | `{"type": "ready"}`. Gọi lại `GET /api/v1/jobs` một lần để lấy những gì đổi trong lúc mất kết nối |
| `ping` | Sau 25 giây không có sự kiện | `{"type": "ping"}`. Chỉ để giữ kết nối, bỏ qua |
| `jobs` | Job của tài khoản thay đổi (gộp khoảng 2 tin/giây) | Xem bên dưới |

```json
{
  "type": "jobs",
  "items": [ { "job_id": "a1b2c3d4", "status": "processing", "progress": 42.5, "step": "...", "...": "..." } ],
  "removed": [],
  "queue_positions": { "a1b2c3d4": 3 }
}
```

- `items`: mỗi phần tử là một job đầy đủ, cùng dạng với phần tử trong `GET /api/v1/jobs` (schema `Job`
  trong contract). Thay job cũ cùng `job_id` bằng phần tử mới. `job_id` chưa có trong danh sách đang
  hiển thị nghĩa là job mới: tải lại danh sách để server áp dụng lọc và sắp xếp.
- `queue_positions`: vị trí trong hàng đợi của các job trong `items`; job không có trong map thì không
  còn xếp hàng (bỏ `queue_position`).
- `removed`: hiện luôn rỗng (job bị xoá được báo bằng `resync`).
- `{"type": "jobs", "resync": true, "items": [], "removed": []}`: quá nhiều thay đổi hoặc có job bị xoá.
  Tải lại danh sách qua REST.

REST là nguồn trạng thái chính. Khi mất kết nối, kết nối lại với backoff (1s, 2s, 4s, … tối đa 30s)
và poll `GET /api/v1/jobs` khoảng 15 giây/lần cho tới khi nhận lại `ready`, giống web.

## Chống tạo trùng job (Idempotency-Key)

`POST /api/v1/batches` và `POST /api/v1/branding/videos` nhận header tuỳ chọn `Idempotency-Key`
(tối đa 128 ký tự). App sinh một UUID cho mỗi lần người dùng bấm gửi và giữ nguyên khi gửi lại sau lỗi
mạng:

- Lần đầu: tạo như bình thường (`201` cho batch, `202` cho branding).
- Gửi lại cùng key trong 24 giờ: `200` với kết quả lần đầu, thêm `"duplicate": true`. Không upload, trừ
  quota hay xếp hàng lần nữa.
- Lần đầu vẫn đang chạy: `409`, chờ rồi gửi lại.
- Lần đầu bị từ chối (4xx/5xx), hoặc job tạo ra đã `failed` hay bị xoá: key được dùng lại để tạo mới.

Key tính riêng theo tài khoản và theo endpoint. Render truyện tranh
(`POST /api/v1/comic/projects/{job_id}/render`) đã có header này từ trước.

## Kiểu dữ liệu trong contract

Các endpoint app dùng đầu tiên đã có schema response: `AuthToken` (`/auth/token`, `/auth/refresh`),
`AuthStatus`, `Account` (`GET`/`PUT /me`), `Job`, `JobPage` (`GET /jobs`), `CreatedBatch`, `Batch`,
`BatchPage`, `QueuedJob`. Mọi schema cho phép trường thêm: payload job lớn dần theo pipeline, nên
model Dart phải bỏ qua trường lạ thay vì báo lỗi.

## Video và file kết quả

`GET /api/v1/jobs/{job_id}/artifacts/video` (cùng `subtitle`, `translation`, `tiktok-json`,
`tiktok-text`) yêu cầu header Bearer. Với `video_player`:

```dart
VideoPlayerController.networkUrl(uri, httpHeaders: {'Authorization': 'Bearer $accessToken'});
```

## Kết nối TikTok, Instagram, Facebook, YouTube, Threads từ app

1. Khai báo deep link của app ở backend (`.env.production`), rồi tạo lại container `vidtrans`:

   ```env
   VIDTRANS_APP_REDIRECT_URIS=vidtrans://oauth
   ```

   Có thể khai báo nhiều link, ngăn cách bằng dấu phẩy, kể cả universal link `https://...`. Chỉ link
   có trong danh sách mới được chấp nhận; so khớp theo phần trước dấu `?`.
2. App gọi `GET /api/v1/<platform>-auth/connect?return_to=vidtrans://oauth` (có Bearer), nhận
   `authorization_url`, mở bằng `flutter_web_auth_2` hoặc trình duyệt hệ thống.
3. Người dùng đồng ý trên trang của nền tảng; nền tảng gọi callback của VidTrans. VidTrans lưu kết nối
   rồi chuyển hướng về `vidtrans://oauth?<platform>=connected`, hoặc
   `vidtrans://oauth?<platform>_error=<lý do>`.
4. App đọc query của deep link, rồi gọi `GET /api/v1/<platform>-auth` để tải danh sách tài khoản.

Redirect URI đăng ký với TikTok, Meta, Google vẫn là callback của VidTrans
(`https://<tên miền>/api/v1/<platform>-auth/callback`), không phải deep link của app.

## Thông báo đẩy (push) khi job xong

Server gửi push qua Firebase Cloud Messaging (HTTP v1) khi job chuyển sang `completed` hoặc `failed`.

1. Sau khi đăng nhập (và mỗi khi FCM đổi token), app đăng ký thiết bị:

   ```http
   POST /api/v1/me/devices
   Content-Type: application/json

   {"token": "<FCM token>", "platform": "android", "app_version": "1.0.0"}
   ```

   Trả `{device_id, platform, app_version, created_at, updated_at, push_enabled}`. Gọi lại với cùng
   token chỉ cập nhật bản ghi cũ; token đăng nhập tài khoản khác sẽ chuyển sang tài khoản đó.
   `push_enabled: false` nghĩa là server chưa cấu hình FCM. Tài khoản `viewer` cũng được đăng ký.
2. Khi đăng xuất: `DELETE /api/v1/me/devices/{device_id}` (trước khi xoá token đăng nhập).
   `GET /api/v1/me/devices` liệt kê thiết bị của tài khoản.
3. Nội dung push: `notification` {title, body} và `data`
   `{"type": "job_status", "job_id": "...", "status": "completed" | "failed"}`. Bấm vào thông báo thì
   mở `/jobs/{job_id}`.

Mỗi kết quả của một lần chạy chỉ được gửi một lần: worker khởi động lại ghi lại `completed` không gửi
thêm; `retry` mở lượt mới nên lần hoàn thành sau sẽ được gửi. Token FCM báo `UNREGISTERED` bị xoá.

Cấu hình server: `VIDTRANS_FCM_CREDENTIALS_FILE=/run/secrets/fcm-service-account.json` (file service
account của Firebase project, quyền "Firebase Cloud Messaging API Admin"). Không đặt biến này thì
không gửi push, mọi thứ khác chạy bình thường.

## Phân quyền

Tài khoản `viewer` chỉ được gọi các request đọc (GET). Mỗi tài khoản chỉ thấy job, batch và kết nối
mạng xã hội của chính mình, như trên web.
