# Test cases — Facebook và YouTube publishing

| TC | AC/NFR | Ca kiểm thử | Kết quả mong đợi | Trạng thái |
|---|---|---|---|---|
| TC_001 | AC01_01 | OAuth state Facebook sai | Từ chối, không lưu token | PASS |
| TC_002 | AC02_02 | Facebook start/upload/finish | Checkpoint video ID, trạng thái processing | PASS |
| TC_003 | BR02_01 | YouTube title/privacy và upload | Trả video ID, privacy trong metadata | PASS |
| TC_004 | AC02_02 | YouTube reconcile processed | Chuẩn hóa `PUBLISHED` | PASS |
| TC_005 | AC02_01 | Generic idempotency | Cùng key không tạo attempt thứ hai | PASS |
| TC_006 | NFR_03 | Provider timeout bằng mock | Attempt lỗi/reconcile an toàn | PASS |
| TC_007 | NFR_04 | Frontend/static regression | Selector/contract hiện có không vỡ | PASS |
| TC_008 | External | Facebook Page thật | Reel xuất hiện đúng Page | NOT_RUN |
| TC_009 | External | YouTube channel thật | Video private upload và xử lý xong | NOT_RUN |

