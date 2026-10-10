# Test cases — TikTok Publishing V2

| TC ID | UC/BR/AC | Nhóm | Dữ liệu/Bước chính | Kết quả mong đợi | Trạng thái |
|---|---|---|---|---|---|
| TC_001 | UC_01/BR01_02/AC01_01 | P1 Functional | Caption nhiều dòng + hashtag, direct publish | Payload giữ nguyên newline/hashtag | PASS |
| TC_002 | UC_01/BR01_04/AC01_02 | P1 Idempotency | Gửi hai lần cùng key | Một attempt, lần hai trả record cũ | PASS |
| TC_003 | UC_01/BR01_04 | P1 Security | Dùng key của video A cho video B | Từ chối | PASS |
| TC_004 | UC_01/AC01_02 | P1 Duplicate | Key mới khi cùng job còn active | Từ chối | PASS |
| TC_005 | UC_02/BR02_01 | P1 Boundary | Lịch hợp lệ tương lai | `SCHEDULED_LOCAL`, UTC | PASS |
| TC_006 | UC_02/BR02_02/AC02_02 | P1 Concurrency | Claim lịch đến hạn hai lần | Chỉ lần đầu nhận attempt | PASS |
| TC_007 | UC_02/BR02_04/AC02_03 | P1 Cancel | Hủy trước claim | `CANCELLED`, không publish | PASS |
| TC_008 | UC_03/BR03_01 | P1 Functional | Upload draft | `SEND_TO_USER_INBOX` | PASS |
| TC_009 | UC_04/BR04_01/AC04_01 | P1 Recovery | Timeout sau init callback | Lưu remote ID, `NEEDS_RECONCILIATION` | PASS |
| TC_010 | NFR_03/AC04_02 | P1 Security | Đọc API attempt | Không lộ server path/idempotency key | PASS |
| TC_011 | Regression | P1 | Publisher/job store/browser tests | Luồng cũ không regression | PASS |
| TC_012 | UI | P2 | JS syntax/browser-frame | Module hợp lệ, frame tests pass | PASS |
| TC_013 | Full suite | P2 | unittest discovery | 1 lỗi import Douyin ngoài feature | BLOCKED |
