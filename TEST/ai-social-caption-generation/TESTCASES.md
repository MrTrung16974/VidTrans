# Test cases — AI social caption generation

| TC | Nội dung | Kết quả | Trạng thái |
|---|---|---|---|
| TC_001 | Model trả 3 variants grounded | 3 caption hợp lệ | PASS |
| TC_002 | Hashtag ngoài candidate | Bị loại | PASS |
| TC_003 | Claim số không có nguồn | Variant reject, fallback | PASS |
| TC_004 | Cue index sai | Chuyển provider kế tiếp | PASS |
| TC_005 | Provider timeout | Local fallback | PASS |
| TC_006 | Tone không hợp lệ | HTTP 422 | PASS |
| TC_007 | API client cũ | Vẫn có `caption`/`generator` | PASS |
| TC_008 | UI tone/variant/provider | Asset và handler tồn tại | PASS static |
| TC_009 | Ownership/artifact không đổi | Regression route | PASS |
| TC_010 | Android parse variants/tag và contract cũ | Flutter unit | PASS |
| TC_011 | Android build toàn app | Debug APK build | PASS |

