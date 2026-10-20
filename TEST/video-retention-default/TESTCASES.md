# Test cases — Mặc định retention 30 ngày

| TC | Trường hợp | Kết quả mong đợi | Trạng thái |
|---|---|---|---|
| TC_RET_001 | Không đặt biến retention | Retention bật, default 30 ngày | PASS |
| TC_RET_002 | `VIDTRANS_RETENTION_ENABLED=0` | Retention tắt, default API trả `null` | PASS |
| TC_RET_003 | Policy riêng theo tài khoản | Override 0 hoặc số ngày riêng được giữ | PASS |
| TC_RET_004 | Quota/audit retention regression | Không regression store/governance | PASS |
