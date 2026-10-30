# VidTrans Mobile (Flutter)

App Android/iOS dùng chung backend FastAPI với web. Hợp đồng API: `../contracts/openapi.json`,
hướng dẫn tích hợp: `../docs/mobile-api.md`.

- Bundle ID / applicationId: `cloud.vidtrans.app`
- Deep link OAuth: `vidtrans://oauth` (khai báo ở backend qua `VIDTRANS_APP_REDIRECT_URIS`)

## Chạy

```bash
flutter pub get
flutter run                                   # backend local: 10.0.2.2:5210 (Android) / 127.0.0.1:5210 (iOS)
flutter run --dart-define=VIDTRANS_API_BASE=https://vidtrans.cloud
flutter test && flutter analyze
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
