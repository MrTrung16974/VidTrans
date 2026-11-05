# Test cases — Subtitle readability scaling

| TC ID | AC | Nhóm | Dữ liệu | Kết quả mong đợi | Trạng thái |
|---|---|---|---|---|---|
| TC_001 | AC01_01 | Unit/P0 | OCR nhỏ, 1080×1920, min 22 | Font 59px | PASS |
| TC_002 | AC01_02 | Regression/P0 | OCR nhỏ, 1280×720, min 22 | Font 22px | PASS |
| TC_003 | AC01_03 | Regression/P0 | Cue Việt rất dài, 1658×1080 | Không mất chữ/tràn; tối đa 3 dòng | PASS |
| TC_004 | AC01_04 | Production visual/P0 | Job `4037ce00` | Preview dễ đọc hơn bản fs22 | PASS |
| TC_005 | NFR_01 | Smoke/P0 | API/portal sau deploy | API sẵn sàng; HTTP 200; không có lỗi mới | PASS |

