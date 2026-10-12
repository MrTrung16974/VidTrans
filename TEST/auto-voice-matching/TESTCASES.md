# Test cases — Tối ưu khớp giọng tự động

| TC ID | AC | Trường hợp | Kỳ vọng | Trạng thái |
|---|---|---|---|---|
| TC_001 | AC01_01 | 125 Hz và 220 Hz rõ | Male/female đúng | PASS |
| TC_002 | AC01_02 | Cue im lặng giữa hai cue male | Cue giữa chọn neighbor male | PASS |
| TC_003 | AC01_02 | Một outlier yếu giữa chuỗi female | Không đổi giọng hai lần | PASS |
| TC_004 | AC01_03 | Chuỗi male rồi female rõ | Giữ chuyển người nói | PASS |
| TC_005 | AC01_04 | Không cue có tiếng | Dùng fallback | PASS |
| TC_006 | AC01_05 | Cue có khoảng nghỉ thật phía sau | Mượn một phần pause, vẫn chừa transition gap | PASS |
| TC_007 | AC01_05 | Cue kế tiếp chồng thời gian | Không mượn pause | PASS |
| TC_008 | AC01_06 | TTS dài hơn nhiều so với cue | Tempo không vượt giới hạn tự nhiên | PASS |
| TC_009 | AC01_07 | Chọn `deep` | Request lưu preset và TTS nhận tuning trầm/chậm | PASS cấp unit/catalog |
| TC_010 | AC01_07 | Gửi preset ngoài allowlist | Backend từ chối validation | PASS |
| TC_011 | AC01_07 | Resume job cũ không có preset | Dùng `natural` mặc định | PASS cấp code review; integration NOT_RUN |
