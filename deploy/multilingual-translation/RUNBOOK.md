# Runbook

## Precheck

- Không có job đang xử lý hoặc đã có kế hoạch dừng/retry.
- Backup volume uploads/outputs/work và database job.
- Docker có đủ dung lượng cho full build.

## Triển khai

1. Cập nhật source tới commit đã duyệt.
2. Chạy `bash docker-rebuild.sh --full` trên VPS.
3. Kiểm tra container app/browser healthy.
4. Kiểm tra OpenAPI có hai form field và giao diện hiển thị 8 ngôn ngữ.
5. Trong app container, `fc-match 'Noto Sans Thai'` và `fc-match 'Noto Sans CJK SC'` phải trả font Noto tương ứng.

## Smoke test

- Job cũ retry vẫn dùng Trung → Việt.
- Job mới source auto → Việt lưu detected language.
- Job Anh → Nhật tạo SRT và video.
- Mode lồng tiếng target Thái tạo audio bằng đúng voice profile hoặc fallback gTTS Thai.

## Dừng/rollback

- Dừng rollout nếu API không startup, font thiếu, job legacy không retry hoặc artifact metadata sai.
- Rollback image/source về commit trước; job JSON mới có field additive nên bản cũ có thể bỏ qua.
- Không xóa job/output khi rollback.
