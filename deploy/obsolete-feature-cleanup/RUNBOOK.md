# Runbook — Migrate và drop `tiktok_publish_attempts`

1. Dừng writer phiên bản cũ và chụp backup database có khả năng restore.
2. Chạy `sql/00_precheck.sql`; dừng nếu thiếu bảng đích hoặc có orphan job chưa được quyết định owner.
3. Chạy `sql/01_migrate_tiktok_attempts.sql`; script tự rollback nếu count đích thấp hơn nguồn.
4. Deploy code mới và smoke test create/list/refresh/cancel TikTok qua social API.
5. Xác nhận không còn process phiên bản cũ, sau đó chạy `sql/02_backup_and_drop.sql`.
6. Chạy `sql/03_postcheck.sql`; theo dõi scheduler và lỗi publish.
7. Nếu cần rollback schema, chạy `sql/rollback.sql` rồi rollback application. Backup table chỉ xoá ở change riêng sau thời gian lưu được phê duyệt.

Không chạy SQL trực tiếp trên production nếu chưa xác minh DSN/schema, backup và người chịu trách nhiệm.
