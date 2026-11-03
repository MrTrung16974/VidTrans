# VidTrans Mobile (Flutter)

App Android/iOS dùng chung backend FastAPI với web. Hợp đồng API: `../contracts/openapi.json`,
hướng dẫn tích hợp: `../docs/mobile-api.md`.

- Bundle ID / applicationId: `cloud.vidtrans.app`
- Deep link OAuth: `vidtrans://oauth` (khai báo ở backend qua `VIDTRANS_APP_REDIRECT_URIS`)

## Chạy

```bash
flutter pub get
adb reverse tcp:5210 tcp:5210                 # Android thật hoặc emulator qua USB/ADB
flutter run                                   # backend local: 127.0.0.1:5210
flutter run --dart-define=VIDTRANS_API_BASE=https://vidtrans.cloud
flutter test && flutter analyze
```

Sau khi rút/cắm lại điện thoại hoặc ADB khởi động lại, chạy lại `adb reverse`.
Build production phải truyền URL HTTPS bằng `VIDTRANS_API_BASE`; địa chỉ mặc định
chỉ dành cho phát triển local.

Với điện thoại Android thật, có thể dùng script để tự thiết lập lại cổng trước
mỗi lần chạy, tránh lỗi "không kết nối được máy chủ" sau khi ADB đổi transport:

```bash
./run-android.sh
# hoặc chọn rõ thiết bị
./run-android.sh R5CTC1L8Z5Z
```

Mạng công ty chặn SSL: Gradle cần truststore có `secrets/mcredit-root-ca.crt`, ví dụ
`JAVA_TOOL_OPTIONS="-Djavax.net.ssl.trustStore=<cacerts có CA> -Djavax.net.ssl.trustStorePassword=changeit"`.
Build iOS cần Xcode đầy đủ (không chỉ Command Line Tools).

## Cấu trúc

```
lib/
  app/        router (go_router), theme
  core/       api (Dio + refresh 1 lần), auth (session), storage (secure tokens),
              websocket (job events + reconnect), errors, widgets
  features/   authentication, jobs, profile, home (mỗi feature: data / domain / presentation)
```
