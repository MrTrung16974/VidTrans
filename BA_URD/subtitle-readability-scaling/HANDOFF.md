# Handoff — Subtitle readability scaling

- Workspace: VidTrans; cập nhật 2026-11-05.
- Mục tiêu: sửa phụ đề quá nhỏ trên video độ phân giải cao.
- Baseline production: job `4037ce00`, 1080×1920, 3 cue đều 22px.
- Đã sửa scale min/max font theo chuẩn 720p; baseline mới 59px.
- Local và production unit test: 11/11 PASS; smoke API/web/HTTPS PASS; log sạch.
- Đã deploy VPS `169.58.116.223` bằng `docker-rebuild.sh`.
- Preview 59px đã kiểm tra trực quan. Output job cũ giữ nguyên; video mới sẽ dùng logic mới.
- Việc tùy chọn tiếp theo: người dùng retry job cũ trong UI nếu muốn artifact chính thức mới.
- Không có DB migration, SQL, Camunda hay secret/config change.

