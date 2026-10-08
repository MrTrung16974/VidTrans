# Manifest

- Feature: multilingual-translation
- DB migration: N/A — language fields nằm trong JSON job/config hiện hữu.
- Camunda: N/A.
- Dependency image: Dockerfile thêm `fonts-noto-core`; CJK dùng font Noto CJK SC được bundle sẵn; lần triển khai đầu cần full build.
- Source: domain, ASR, translator, pipeline, API/TTS, frontend, tests và tài liệu feature.

## Thứ tự

1. Backup volume dữ liệu job.
2. Full build image để cài font.
3. Khởi động container.
4. Smoke test OpenAPI/UI.
5. Tạo job ngắn `auto → vi` và `en → ja` ở mode phụ đề.
6. Tạo job lồng tiếng ngắn cho một target không phải Việt.
