# Test cases — Cleanup stack TikTok cũ

| TC | Trường hợp | Mong đợi | Trạng thái |
|---|---|---|---|
| TC_CLEAN_001 | TikTok direct/draft/schedule qua UI | Chỉ gọi social publish API, mode được map đúng | PASS |
| TC_CLEAN_002 | Tenant khác đọc attempt | 404, không lộ attempt | PASS |
| TC_CLEAN_003 | Provider timeout sau remote side effect | Chuyển reconciliation, không tự đăng trùng | PASS |
| TC_CLEAN_004 | Migration terminal/scheduled/remote/unknown | Đủ 4 row, trạng thái an toàn, idempotent | PASS trên PostgreSQL tạm |
| TC_CLEAN_005 | Backup và drop bảng legacy | Backup đủ row rồi mới DROP | PASS trên PostgreSQL tạm |
| TC_CLEAN_006 | Rollback | Khôi phục bảng và đủ 4 row | PASS trên PostgreSQL tạm |
| TC_CLEAN_007 | OCR config | Chỉ còn backend mới, ARM dùng Tesseract | PASS |
| TC_CLEAN_008 | TikTok browser-assisted | Không regression, bảng `attempts` vẫn dùng | PASS |
