# Runbook — Subtitle readability scaling

## Đã thực hiện

1. Xác minh VPS sạch tại revision `3649d55c` đối với hai file thay đổi.
2. Đồng bộ source/test.
3. Chạy `bash docker-rebuild.sh` (không full dependency rebuild).
4. Chạy unit trong container, tính baseline 59px, smoke API/web/HTTPS và kiểm tra log.

## Rollback

Khôi phục hai file từ revision `3649d55c`, chạy lại `bash docker-rebuild.sh`, sau đó lặp smoke test. Output đã tạo trước/sau thay đổi không bị tự động xóa hoặc ghi đè.

