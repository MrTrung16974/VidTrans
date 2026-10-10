# Deployment manifest

- Artifact type: source rollout, chưa triển khai môi trường thật.
- Schema: additive table `tiktok_publish_attempts`, được tạo idempotent lúc app khởi động.
- Code: application service/routes, SQLite store, publisher callback, FastAPI wiring, frontend controls.
- Secret/config: OAuth account cần scopes `video.upload,video.publish`; không lưu giá trị trong repo.
- Không có Camunda/BPMN/DMN.

## Thứ tự

1. Backup `work/jobs.sqlite3`.
2. Deploy backend/frontend cùng phiên bản.
3. Khởi động một instance và kiểm tra table/index.
4. Kết nối lại TikTok để cấp `video.publish` nếu cần.
5. Smoke test bằng `SELF_ONLY` trên account test.
6. Mở traffic sau khi xác nhận reconciliation.
