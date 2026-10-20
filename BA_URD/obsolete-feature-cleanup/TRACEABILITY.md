# Traceability — Cleanup stack TikTok cũ

| Yêu cầu | Code/artifact | Test | Trạng thái |
|---|---|---|---|
| Một stack social publishing | `main.py`, `tiktok.js`, `social_publishing.py` | TC_CLEAN_001–003 | PASS |
| Xoá code không dùng | Xoá router/service/store/scheduler TikTok cũ | Static search + 87 regression | PASS |
| Migrate/drop table cũ | `DEPLOY/.../sql/00–03`, `rollback.sql` | TC_CLEAN_004–006 | PASS disposable; production NOT_RUN |
| Xoá OCR compatibility | `app/config.py`, harness/test | TC_CLEAN_007 | PASS |
| Giữ TikTok browser | `tiktok_browser.py`, routes | TC_CLEAN_008 | PASS |
