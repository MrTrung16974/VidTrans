# Implementation — AI social caption generation

- Thêm `application/social_caption_suggestions.py`: prompt/schema, 3 variants, guardrail, provider chain và local fallback.
- Mở rộng route caption với `tone` và response variants nhưng giữ `caption`/`generator`.
- UI thêm 5 tone, variant tabs và badge provider/fallback.
- Thêm unit/API/frontend regression tests.
- Android Flutter: `CaptionSuggestions` parse response mới/cũ; màn hình đăng bài có chọn tone, variant chips, hashtag chips và nút áp dụng tường minh.

## Kiểm chứng local

- `python3 -m unittest tests.test_social_caption_suggestions tests.test_tiktok_summary tests.test_tiktok_browser tests.test_frontend_static`: 64/64 PASS.
- `git diff --check`: PASS trước lần chạy `node --check`.
- `node --check web/js/tiktok.js`: BLOCKED do file là ES module nhưng repo không khai báo Node package type; frontend static tests PASS.
- `cd mobile && flutter test test/features/social_publishing/caption_suggestions_test.dart test/features/social_publishing/social_models_test.dart`: 11/11 PASS.
- `cd mobile && flutter build apk --debug`: PASS; APK debug được tạo.

## Deploy

- Lần đồng bộ đầu bị `scp: Connection closed`; chưa rebuild.
- Portal production vẫn HTTPS 200.
- SSH sau đó timeout ở cổng 22; cần retry khi SSH phục hồi.

