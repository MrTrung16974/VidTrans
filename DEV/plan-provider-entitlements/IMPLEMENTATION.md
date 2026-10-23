# Kết quả triển khai

- `backend/domain/plan_entitlements.py`: bảng provider trả phí, `provider_allowed`, `ensure_*`, `annotate_providers`.
- `backend/main.py`: kiểm tra ở `/convert`, `/api/v1/batches`, `/api/v1/jobs/{id}/retry`; `/api/v1/tts-providers`, `/api/v1/translation-providers` thêm `allowed`.
- `backend/application/comic_routes.py`: `/options` thêm `allowed`; tạo dự án (sau kiểm tra cấu hình) và render kiểm tra gói; "auto" của Free bỏ Gemini.
- `web/js/app.js`, `web/js/comic.js`: khóa option bị giới hạn; ảnh comic mặc định không chọn Gemini khi bị khóa; thẻ gói ghi quyền lợi AI.
- `backend/tests/test_elevenlabs_voice_api.py`: tài khoản test chuyển sang gói Pro (test về chọn giọng).
- `contracts/openapi.json`: regenerate.
