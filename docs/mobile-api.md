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

Token sai hoặc hết hạn thì server đóng kết nối với mã `4401`: làm mới token rồi kết nối lại. Định
dạng sự kiện giống web, xem `web/js/job-events.js`.

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

## Phân quyền

Tài khoản `viewer` chỉ được gọi các request đọc (GET). Mỗi tài khoản chỉ thấy job, batch và kết nối
mạng xã hội của chính mình, như trên web.
