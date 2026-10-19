# Manifest — Cleanup stack TikTok cũ

- Runtime source: `backend/main.py`, `backend/app/config.py`, `backend/frontend/js/{app,tiktok}.js`, `backend/infrastructure/{job_store,social_publish_store}.py`.
- Deleted runtime modules: TikTok publish route/service/store riêng.
- SQL order: `00_precheck.sql` → `01_migrate_tiktok_attempts.sql` → deploy code → `02_backup_and_drop.sql` → `03_postcheck.sql`.
- Rollback: `rollback.sql` + application version trước.
- PostgreSQL disposable verification: PASS.
- Production execution/UAT: NOT_RUN.
