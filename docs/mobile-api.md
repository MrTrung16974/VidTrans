# API cho ứng dụng di động (Flutter)

Web và app iOS/Android dùng chung một API. Hợp đồng nằm ở `contracts/openapi.json`; app Flutter
sinh client Dart từ file này, không dùng chung code với web.

| Môi trường | Địa chỉ API |
| --- | --- |
| Production | `https://<tên miền>/api/v1/...` (cùng tên miền với web) |
| Local, Docker | `http://localhost:5210` (chỉ API) hoặc `http://localhost:5200` (qua container web) |
| Android thật/emulator qua ADB | `http://127.0.0.1:5210` sau `adb reverse tcp:5210 tcp:5210` |
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

## Tiến trình job và thông báo theo thời gian thực (Socket.IO)

App dùng Socket.IO (gói `socket_io_client`) ở path `/api/v1/socket.io`, chỉ transport `websocket`.
Token gửi trong auth payload, đọc lại mỗi lần kết nối để dùng token đã làm mới:

```dart
final socket = io.io('https://vidtrans.cloud', io.OptionBuilder()
    .setPath('/api/v1/socket.io')
    .setTransports(['websocket'])
    .setAuthFn((send) => tokens.read().then((t) => send({'token': t?.accessToken ?? ''})))
    .build());
```

Token sai hoặc thiếu: server từ chối, client nhận `connect_error` với message `unauthorized`. Token hết hạn
khi đang kết nối: server gửi `auth_expired` rồi ngắt. Cả hai trường hợp: làm mới token rồi kết nối lại.
App tự kết nối lại với backoff (1s, 2s, 4s, … tối đa 30s) và trong lúc mất kết nối tải lại
`GET /api/v1/jobs` khoảng 15 giây/lần. Code ở `mobile/lib/core/websocket/job_events.dart`.

### Sự kiện

| Sự kiện | Khi nào | Nội dung |
| --- | --- | --- |
| `connect` (của Socket.IO) | Kết nối xong | Gọi lại `GET /api/v1/jobs` một lần để lấy những gì đổi trong lúc mất kết nối |
| `jobs` | Job của tài khoản thay đổi (gộp khoảng 2 tin/giây) | Xem bên dưới |
| `notification` | Job chuyển sang `completed` hoặc `failed` | `{"type": "job_status", "job_id", "status", "title", "body"}`, cùng nội dung với push FCM |
| `auth_expired` | Access token hết hạn | Làm mới token rồi kết nối lại |

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
- `{"type": "jobs", "resync": true, "items": [], "removed": []}`: quá nhiều thay đổi hoặc có job bị xoá.
  Tải lại danh sách qua REST.
- Tài khoản `owner`/`admin` nhận thay đổi của mọi job (như danh sách job của họ); tài khoản khác chỉ nhận
  job của mình. `notification` chỉ gửi cho chủ job.

Khi app mở, một kết quả job có thể đến hai lần (sự kiện `notification` và push FCM): app chỉ hiện một
SnackBar cho mỗi cặp `job_id` + `status`.

REST vẫn là nguồn trạng thái chính.

### WebSocket cũ (web)

Web vẫn dùng `wss://<tên miền>/api/v1/jobs/events` (cookie phiên, cùng định dạng tin `jobs`, thêm
`ready`/`ping`). Endpoint này giữ nguyên; app mới không dùng.

## Chống tạo trùng job (Idempotency-Key)

`POST /api/v1/batches` nhận header tuỳ chọn `Idempotency-Key`
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

## Phân quyền theo gói

`GET /api/v1/me` trả thêm `features`: danh sách tính năng gói của tài khoản được dùng, đọc từ DB
(bảng `plans`, `plan_features`, `plan_entitlements` — xem `backend/infrastructure/plan_store.py`).

| Tính năng (`feature_key`)  | trial | free | creator | pro | enterprise |
| -------------------------- | :---: | :--: | :-----: | :-: | :--------: |
| `comic_video`              |   ✗   |  ✗   |    ✓    |  ✓  |     ✓      |
| `social_publishing`        |   ✗   |  ✗   |    ✓    |  ✓  |     ✓      |
| `ai_translation_gemini`    |   ✗   |  ✗   |    ✓    |  ✓  |     ✓      |
| `ai_tts_elevenlabs`        |   ✗   |  ✗   |    ✓    |  ✓  |     ✓      |
| `ai_comic_script_gemini`   |   ✗   |  ✗   |    ✓    |  ✓  |     ✓      |
| `ai_comic_image_gemini`    |   ✗   |  ✗   |    ✓    |  ✓  |     ✓      |
| `ai_comic_animation_veo`   |   ✗   |  ✗   |    ✓    |  ✓  |     ✓      |

Bảng trên là giá trị mặc định; owner/admin luôn có đủ. Admin đổi trong web: **Quản trị → 🔐 Phân
quyền gói & menu** (ghi vào DB, có audit `plan_entitlement.update`). Backend tự chặn (`403`) mọi thao
tác tạo/sửa/đăng khi gói không có quyền; xem và xoá dữ liệu cũ vẫn được.

### Menu điều hướng từ DB

`GET /api/v1/me/menus?platform=mobile` (hoặc `web`) trả các menu tài khoản được thấy, đã lọc theo
bật/tắt, vai trò tối thiểu và tính năng gói, đúng thứ tự admin cấu hình:

```json
{"platform": "mobile", "dock_size": 4,
 "items": [{"key": "translate", "label": "Dịch", "short_label": "Dịch"}, {"key": "jobs", "...": "..."}]}
```

App hiện `dock_size` mục đầu tiên có màn hình tương ứng (`translate`, `comic`, `jobs`, `social`,
`upgrade`, `profile`), bỏ qua key lạ, và nạp lại khi app quay về foreground. Khi chưa tải được, app dùng
bộ menu mặc định giống dữ liệu seed. Admin sửa tên, tên ngắn, thứ tự, bật/tắt, vai trò tối thiểu và
tính năng yêu cầu của từng menu (bảng `app_menus`, audit `app_menu.update`). Danh sách màn hình là cố
định trong code; menu Quản trị trên web luôn bật và chỉ dành cho quản trị viên.

API quản trị (owner/admin): `GET /api/v1/admin/access-config`,
`PUT /api/v1/admin/plan-entitlements/{plan_key}/{feature_key}` `{"enabled": true}`,
`PUT /api/v1/admin/menus/{platform}/{menu_key}`.

## Dịch ảnh chụp màn hình (giai đoạn 1 của Screen Translator)

App: tab Dịch → **Dịch ảnh chụp màn hình** (`/translate/image`). Cần tính năng gói `image_translation`
(bật sẵn cho mọi gói, kể cả trial/free; admin tắt được). OCR luôn chạy trên điện thoại (Google ML Kit,
model tiếng Trung, đọc được cả chữ Latin); ảnh không rời máy.

- **Online** (mặc định, dịch tốt hơn hẳn): chỉ gửi phần chữ đã đọc được:

```
POST /api/v1/text-translations
{"texts": ["你今天有时间开会吗？"], "source_language": "zh-CN", "target_language": "vi", "provider": ""}
→ {"translations": ["..."], "source_language": "zh-CN", "target_language": "vi", "provider": "google-zh-CN-vi"}
```

- **Offline** (khi không có mạng hoặc không muốn gửi chữ đi): dịch bằng ML Kit trên máy, nhanh (~0,5 s/màn
  hình) nhưng câu khó hay sai nghĩa; lần đầu hỏi trước khi tải gói ngôn ngữ (~30 MB/ngôn ngữ).

`source_language` phải là ngôn ngữ cụ thể (app tự nhận diện trên máy khi chọn "Tự nhận diện"). Tối đa
80 đoạn và 12 000 ký tự mỗi lần. `provider` rỗng = mặc định của máy chủ, tự lùi về Google nếu gói không
dùng được Gemini; chọn thẳng `gemini` khi gói không có `ai_translation_gemini` → `403`. Tài khoản Người
xem gọi được vì endpoint không lưu gì. Lỗi mọi bộ dịch → `502`.

Kiểm thử trên thiết bị: `flutter test integration_test/image_translation_test.dart -d <thiết bị Android>`.
iOS cần `pod install` (Podfile khai báo iOS 15.5 và `GoogleMLKit/TextRecognitionChinese`).

### Nút dịch nổi trên mọi ứng dụng (giai đoạn 2, chỉ Android)

App: Dịch → Dịch ảnh chụp màn hình → **Bật nút dịch nổi**. Dùng chung ngôn ngữ và chế độ Offline/Online
của màn Dịch ảnh (đổi lúc đang bật thì áp dụng ngay).

- Bật lần đầu: quyền **Hiển thị trên ứng dụng khác**; mỗi lần bật: Android hỏi **chia sẻ toàn màn hình**
  (Android 14+ bắt buộc hỏi lại mỗi phiên; app chỉ cho chọn toàn màn hình). Chế độ Offline tải sẵn gói ngôn
  ngữ trước khi bật vì dịch vụ chạy nền không hỏi được.
- Chạm nút **Dịch** nổi → ẩn nút, chụp đúng 1 khung → OCR/dịch bằng chính pipeline của Dịch ảnh trong một
  Flutter engine nền (`screenTranslatorMain`, chạy được cả khi đã đóng app) → lớp phủ thay chữ đúng vị trí.
  **Chạm bất kỳ đâu để đóng.** Chữ trên thanh trạng thái/điều hướng được bỏ qua.
- Không quay video, không lưu ảnh (ảnh JPEG tạm cho OCR bị xoá ngay). Tắt bằng nút trong app hoặc nút
  **Tắt** trên thông báo; dừng chia sẻ màn hình từ hệ thống cũng tắt dịch vụ.
- App chặn chụp màn hình (ngân hàng, video DRM) trả về khung đen → báo “Ứng dụng này chặn chụp màn hình”.

Mã: `android/.../screentranslator/` (ScreenTranslatorService, ScreenCapture, OverlayViews),
`lib/features/screen_translator/`. Kiểm thử trên máy ảo/thiết bị (tự cấp quyền, bấm đồng ý, chạm nút,
chụp màn hình vào `build/screen_translator_test/`):

```
scripts/test_screen_translator.sh emulator-5554
```

**Cuộn trong chế độ Live:** mỗi lần dịch toàn màn hình, khung chụp (sạch, chưa có lớp dịch) làm "khung gốc".
Màn hình đổi → ẩn lớp dịch nhưng giữ bản dịch; đứng yên lại → đo một lần khung gốc → khung hiện tại
(`ScrollEstimator`, 40 cột × mọi hàng, bỏ 20% hàng lệch nhất để không vướng thanh tiêu đề/ô nhập) → đặt lại
bản dịch, kiểm tra từng bản (chữ dưới nó còn đúng không, `regionMatches`, bỏ bản sai như thanh địa chỉ đã ẩn)
→ hiện ngay, rồi chỉ OCR + dịch phần mới lộ ra. Không đo được (đổi trang, cuộn quá nửa màn hình) → dịch lại toàn bộ.
Khoảng chờ "đã dừng" tự điều chỉnh theo nhịp khung (60 fps ≈ 0,16 s; máy chậm tới 0,8 s).

**Dịch liên tục (Live, giai đoạn 3):** công tắc “Tự dịch liên tục” trong thẻ nút nổi, hoặc **nhấn giữ nút
nổi** (nút đổi thành LIVE màu xanh ngọc). Mỗi khung hình mới được so với khung trước bằng ảnh thu nhỏ 72 cột
(bỏ qua thanh hệ thống và nút nổi); màn hình đổi → ẩn bản dịch cũ; đứng yên ~0,35 s → chụp khung sạch, dịch,
hiện bản dịch mới; kết quả về muộn (màn hình đã đổi trong lúc dịch) bị bỏ. Logic nằm ở `LiveScheduler.kt`
(unit test JVM: `cd android && ./gradlew :app:testDebugUnitTest`). Lớp phủ Live **cho chạm/cuộn xuyên qua**
nên Android 12+ buộc độ đục ≤ 80%: chữ gốc hiện mờ phía sau. Chạm nút LIVE = dịch lại ngay. App Cài đặt
của Android (và một số app bảo mật) tự ẩn lớp phủ của app khác. Kiểm thử trên thiết bị (Chrome, tiếng Trung):

```
scripts/test_screen_translator.sh emulator-5554 build/screen_translator_test live
```

Mặc định test dùng bộ dịch giả (`debug-echo`, chỉ có trong bản debug). Dịch thật:
`MODE=offline` (tự tải gói zh/en/vi trước, cần Internet) hoặc `MODE=online` (cần backend ở cổng 5210 và
`adb reverse tcp:5210 tcp:5210`). Máy ảo sau mạng công ty có thể không tải được gói ML Kit; khởi động
máy ảo với `-dns-server 8.8.8.8` hoặc dùng mạng khác.

**Google Play:** trước khi phát hành phải khai báo trong Play Console quyền
`FOREGROUND_SERVICE_MEDIA_PROJECTION` (kèm video demo luồng bật nút → dịch) và giải thích
`SYSTEM_ALERT_WINDOW`; cập nhật chính sách quyền riêng tư phần “nội dung màn hình”.

**Tắt nhanh:** kéo nút nổi (cả hai chế độ) → thùng rác hiện ở cuối màn hình; kéo vào gần sẽ hút vào, thùng
chuyển đỏ; thả ra = tắt dịch (chế độ chụp: dừng service; Trợ năng: `enabled=false`, service vẫn bật trong Cài đặt).

### Chế độ Trợ năng (Accessibility Service, tuỳ chọn)

Trong thẻ nút dịch nổi chọn **Chụp màn hình | Trợ năng**. Ở chế độ Trợ năng, `AccessibilityTranslatorService.kt`
đọc chữ trực tiếp từ cây giao diện của app đang mở (không chụp, không OCR) và vẽ bản dịch lên
`TYPE_ACCESSIBILITY_OVERLAY`: **che kín 100%** mà vẫn chạm/cuộn app bên dưới được, không cần hỏi quyền chia sẻ
màn hình mỗi lần bật.

- Lần đầu: hộp thoại giải thích (prominent disclosure) → mở Cài đặt › Hỗ trợ tiếp cận → bật
  “VidTrans – Dịch màn hình”; quay lại app thì tự bật dịch. Sau đó bật/tắt ngay trong app.
- App ↔ service qua SharedPreferences gốc `screen_translator_a11y` (`enabled`, `source`, `target`, `mode`);
  kênh `vidtrans/screen_translator`: `a11yEnabled`, `a11yConfig`, `setA11yConfig`, `openAccessibilitySettings`.
  Bật chế độ này sẽ tắt nút chụp màn hình đang chạy (hai lớp dịch không chồng nhau).
- Dịch: engine Dart headless, lệnh `translateTexts {texts, source, target, mode}` → `{translations: [...]}`
  (null = không cần dịch: đã đúng ngôn ngữ đích, số, giờ). Cache theo chữ, nên cuộn lại không dịch lại.
- Vị trí: khung chữ thật lấy bằng `EXTRA_DATA_TEXT_CHARACTER_LOCATION_KEY` (Chrome dùng tên AndroidX
  `android.core.view…`), đo một lần khi màn hình đứng yên rồi cache. Khi cuộn: ẩn lớp dịch, chờ cây giao diện
  yên 150 ms và hai lần đọc giống nhau (Chrome cập nhật cây trễ hơn hình) rồi vẽ lại ở vị trí mới, ~0,3–0,6 s.
- Màu: Android 11+ chụp một ảnh (`takeScreenshot`, chỉ khi màn hình đứng yên, bỏ vùng nút nổi) để lấy màu nền
  (màu phổ biến nhất trong khung) và màu chữ (màu tương phản phổ biến nhất); Android 8–10: nền xám nhạt, chữ đen.
- Nút nổi: chạm = tạm dừng/tiếp tục, nhấn giữ = dịch lại màn hình này, kéo xuống thùng rác = tắt dịch. Không đọc ô nhập liệu, mật khẩu, System UI
  và chính app VidTrans.
- Giới hạn: chữ trong ảnh/video/game, app tự vẽ chữ (Flutter không bật semantics, game) thì không có trong cây →
  dùng chế độ Chụp màn hình. Logic thuần ở `A11yText.kt` (unit test `A11yTextTest`).

Thử trên máy ảo không cần bấm tay:

```
adb shell settings put secure enabled_accessibility_services cloud.vidtrans.app/.screentranslator.AccessibilityTranslatorService
```

**Google Play (rủi ro cao):** API Trợ năng chỉ được duyệt khi khai báo rõ trong Play Console (form
AccessibilityService API) + video, có prominent disclosure trong app và trong chính sách quyền riêng tư.
App không phải công cụ hỗ trợ người khuyết tật (`isAccessibilityTool=false`) nên Google xét kỹ; nếu bị từ chối,
gỡ service khỏi manifest bản Play và giữ chế độ Trợ năng cho bản phát hành ngoài (APK trực tiếp).

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

   `platform`: `android`, `ios` hoặc `web`. Web (`web/js/web-push.js`) đăng ký khi trình duyệt đã cho
   phép thông báo; push gửi tới token `web` là data-only (`data` có thêm `title`, `body`) và
   `web/firebase-messaging-sw.js` tự hiển thị, bấm vào mở `/?job=<job_id>#jobs`. Khi đang có tab
   VidTrans trên màn hình thì service worker không hiện gì (tab đã toast qua WebSocket).

   Trả `{device_id, platform, app_version, created_at, updated_at, push_enabled}`. Gọi lại với cùng
   token chỉ cập nhật bản ghi cũ; token đăng nhập tài khoản khác sẽ chuyển sang tài khoản đó.
   `push_enabled: false` nghĩa là server chưa cấu hình FCM. Tài khoản `viewer` cũng được đăng ký.
2. Khi đăng xuất: `DELETE /api/v1/me/devices/{device_id}` (trước khi xoá token đăng nhập).
   `GET /api/v1/me/devices` liệt kê thiết bị của tài khoản.
3. Nội dung push: `notification` {title, body} và `data`
   `{"type": "job_status", "job_id": "...", "status": "completed" | "failed" | "awaiting_input"}`. Bấm vào thông báo thì
   mở `/jobs/{job_id}`.

Mỗi kết quả của một lần chạy chỉ được gửi một lần: worker khởi động lại ghi lại `completed` không gửi
thêm; `retry` mở lượt mới nên lần hoàn thành sau sẽ được gửi. Token FCM báo `UNREGISTERED` bị xoá.

Cấu hình server: `VIDTRANS_FCM_CREDENTIALS_FILE=/run/secrets/fcm-service-account.json` (file service
account của Firebase project, quyền "Firebase Cloud Messaging API Admin"). Không đặt biến này thì
không gửi push, mọi thứ khác chạy bình thường.

## Phân quyền

Tài khoản `viewer` chỉ được gọi các request đọc (GET). Mỗi tài khoản chỉ thấy job, batch và kết nối
mạng xã hội của chính mình, như trên web.

## Tải video mạng xã hội trên điện thoại

Máy chủ tải link TikTok, Douyin bằng IP datacenter nên hay bị nền tảng coi là bot. App tải những link này
ngay trên điện thoại (IP nhà mạng, phiên đăng nhập của người dùng), rồi upload file như video chọn từ máy.

1. `GET /api/v1/download/policy` cho biết nền tảng nào tải trên máy:

   ```json
   {"platforms": {"TikTok": {"mode": "client_then_server"}, "Douyin": {"mode": "client_then_server"},
                  "YouTube": {"mode": "server"}, "Facebook": {"mode": "server"}}}
   ```

   `client_then_server`: app thử tải trên máy, không được thì gửi link cho server như cũ. `server`: chỉ server
   tải. Đổi bằng `VIDTRANS_CLIENT_DOWNLOAD_PLATFORMS` (mặc định `TikTok,Douyin`; để trống thì tắt).
2. Gửi file tải được trong `files` của `POST /api/v1/batches`, kèm `file_source_urls`: mảng JSON có đúng một
   phần tử cho mỗi file, theo cùng thứ tự, là link gốc hoặc `null` với file chọn từ máy:

   ```
   files=<tiktok.mp4>  files=<clip.mp4>  file_source_urls=["https://www.tiktok.com/@a/video/1", null]
   source_links=https://youtu.be/xyz
   ```

   Job của file đó có `source_url`, `source_platform` và `source_origin: "client"`. Server không tự tải lại
   link này khi khởi động lại job.

App mở link trong WebView ẩn (`flutter_inappwebview`), đọc địa chỉ video trong trang, rồi tải bằng
`background_downloader` với cookie, User-Agent và Referer của WebView. Các link tải lần lượt, nghỉ khoảng
1,5 giây giữa hai link.
