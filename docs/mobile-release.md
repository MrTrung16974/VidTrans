# Phát hành app VidTrans (TestFlight và Google Play Internal Testing)

App: `mobile/`, bundle ID / applicationId `cloud.vidtrans.app`, deep link `vidtrans://`.
API: `docs/mobile-api.md`. CI: `.github/workflows/mobile.yml` (contract, analyze, test, APK debug,
build iOS không ký).

## 1. Chuẩn bị một lần

### Firebase (push)

1. Tạo project ở <https://console.firebase.google.com>, thêm app Android `cloud.vidtrans.app` và app
   iOS `cloud.vidtrans.app`. Không cần tải `google-services.json` / `GoogleService-Info.plist`: app
   đọc cấu hình từ `--dart-define`.
2. Ghi lại các giá trị (Project settings → General):

   | dart-define | Lấy từ |
   | --- | --- |
   | `FIREBASE_API_KEY` | Web API Key |
   | `FIREBASE_PROJECT_ID` | Project ID |
   | `FIREBASE_MESSAGING_SENDER_ID` | Project number / Sender ID (tab Cloud Messaging) |
   | `FIREBASE_ANDROID_APP_ID` | App ID của app Android (`1:...:android:...`) |
   | `FIREBASE_IOS_APP_ID` | App ID của app iOS (`1:...:ios:...`) |

   Lưu vào file `mobile/firebase.env.json` (đã có trong `.gitignore`, không commit):

   ```json
   {
     "FIREBASE_API_KEY": "...",
     "FIREBASE_PROJECT_ID": "...",
     "FIREBASE_MESSAGING_SENDER_ID": "...",
     "FIREBASE_ANDROID_APP_ID": "...",
     "FIREBASE_IOS_APP_ID": "...",
     "VIDTRANS_API_BASE": "https://vidtrans.cloud"
   }
   ```

3. iOS: Apple Developer → Keys → tạo key "Apple Push Notifications service (APNs)" (.p8), rồi
   Firebase → Project settings → Cloud Messaging → Apple app configuration → upload key, Key ID,
   Team ID.
4. Server: Firebase → Project settings → Service accounts → Generate new private key. Đặt file lên
   VPS (ví dụ `secrets/fcm-service-account.json`, mount vào container) và khai báo trong
   `.env.production`:

   ```env
   VIDTRANS_FCM_CREDENTIALS_FILE=/run/secrets/fcm-service-account.json
   VIDTRANS_APP_REDIRECT_URIS=vidtrans://oauth
   ```

   Tạo lại container `vidtrans`. `GET /api/v1/me/devices` trả `push_enabled: true` là đã nhận.

Thiếu cấu hình Firebase thì app vẫn chạy, chỉ tắt push.

### Android: khoá ký (upload key)

```bash
keytool -genkey -v -keystore ~/vidtrans-upload.jks -keyalg RSA -keysize 2048 -validity 10000 -alias upload
```

Tạo `mobile/android/key.properties` (đã gitignore; giữ bản sao khoá ở nơi an toàn, mất khoá thì phải
xin Google reset upload key):

```properties
storeFile=/Users/<bạn>/vidtrans-upload.jks
storePassword=...
keyAlias=upload
keyPassword=...
```

Không có `key.properties` thì bản release ký bằng khoá debug và Google Play sẽ từ chối.

### iOS: tài khoản Apple

- Apple Developer Program (99 USD/năm), Xcode đầy đủ (không chỉ Command Line Tools):
  `sudo xcode-select -s /Applications/Xcode.app`.
- Mở `mobile/ios/Runner.xcworkspace` → target Runner → Signing & Capabilities: chọn Team, giữ bundle
  ID `cloud.vidtrans.app`, kiểm tra có capability **Push Notifications** và **Background Modes**
  (Remote notifications, Background fetch). Các mục này đã khai trong `Runner.entitlements` và
  `Info.plist`.
- App Store Connect → My Apps → New App với bundle ID trên.

## 2. Mỗi lần phát hành

Tăng `version:` trong `mobile/pubspec.yaml` (ví dụ `1.0.1+2`; số sau `+` phải tăng mỗi lần upload).

### Google Play Internal Testing

```bash
cd mobile
flutter build appbundle --release --dart-define-from-file=firebase.env.json
# -> build/app/outputs/bundle/release/app-release.aab
```

1. Play Console → tạo app (lần đầu), điền Store listing tối thiểu, Data safety, Content rating.
2. Testing → Internal testing → Create new release → upload `.aab` (lần đầu bật Play App Signing).
3. Thêm email tester → Save → Review release → Start rollout. Tester mở link opt-in rồi cài từ Play.

### TestFlight

```bash
cd mobile
flutter build ipa --release --dart-define-from-file=firebase.env.json
# -> build/ios/ipa/*.ipa
```

1. Upload bằng app **Transporter** (kéo file `.ipa`) hoặc
   `xcrun altool --upload-app -f build/ios/ipa/*.ipa -t ios --apiKey <KEY_ID> --apiIssuer <ISSUER_ID>`.
2. App Store Connect → TestFlight: chờ build xử lý xong, trả lời câu hỏi mã hoá (đã khai
   `ITSAppUsesNonExemptEncryption = false`), thêm Internal Testers (tối đa 100, không cần duyệt).
3. External testers cần Beta App Review lần đầu.

## 3. Kiểm thử trước UAT

- Đăng nhập, tạo job, khoá màn hình/đưa app ra nền: job vẫn chạy trên server, push đến khi xong.
- Bấm push → mở đúng chi tiết job. Mở `vidtrans://jobs/<id>` từ Notes/Safari cũng mở job.
- Đăng xuất → `GET /api/v1/me/devices` (bằng tài khoản đó) không còn thiết bị.
- Mạng yếu: bật Network Link Conditioner (iOS) / giới hạn băng thông emulator, upload video lớn,
  tắt mạng giữa chừng rồi gửi lại: không tạo job trùng (Idempotency-Key).
- Tài khoản `viewer`: không có nút tạo/hủy/chạy lại.

## 4. Mạng công ty (proxy chặn SSL)

Gradle cần tin CA `secrets/mcredit-root-ca.crt`:

```bash
JH=$(/usr/libexec/java_home -v 17)
cp "$JH/lib/security/cacerts" ~/vidtrans-cacerts && chmod u+w ~/vidtrans-cacerts
keytool -importcert -noprompt -alias mcredit-root -file secrets/mcredit-root-ca.crt \
  -keystore ~/vidtrans-cacerts -storepass changeit
export JAVA_TOOL_OPTIONS="-Djavax.net.ssl.trustStore=$HOME/vidtrans-cacerts -Djavax.net.ssl.trustStorePassword=changeit"
```
