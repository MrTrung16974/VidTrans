# Test cases — Dịch đa ngôn ngữ

- URD: 1.0
- Dữ liệu: câu tổng hợp, không chứa dữ liệu thật.
- Provider/Whisper/TTS được mock trong unit test.

| TC ID | UC/BR/AC | Nhóm | Dữ liệu | Kết quả mong đợi | Trạng thái |
|---|---|---|---|---|---|
| TC_001 | UC01/BR01 | Functional | source `auto`, target `ja` | hợp lệ | PASS |
| TC_002 | UC01/BR02 | Validation | target `auto` | từ chối | PASS |
| TC_003 | UC01/BR03 | Validation | source=target=`en` | từ chối | PASS |
| TC_004 | UC01/BR06 | Validation | OCR burned + source `en` | từ chối | PASS |
| TC_005 | UC04/BR17 | Regression | request cũ thiếu language | mặc định `zh-CN/vi` | PASS |
| TC_006 | UC02/BR07 | Unit | source `ja` | Whisper nhận `language=ja` | PASS |
| TC_007 | UC02/BR08 | Unit | source auto, model detect ja | trả ja + probability | PASS |
| TC_008 | UC02/BR09 | Unit | en → ja | URL provider có `sl=en`, `tl=ja` | PASS |
| TC_009 | UC02/BR10 | Unit | Google 429 | chuyển MyMemory | PASS |
| TC_010 | UC02/BR11 | Regression | target zh-CN, output chữ Hán | chấp nhận | PASS |
| TC_011 | UC03/BR14 | Unit | 8 profiles | mỗi profile có voice nam/nữ | PASS |
| TC_012 | UC03/BR15 | Unit | 8 profiles | mỗi profile có gTTS locale | PASS |
| TC_013 | API | Integration | OpenAPI local | có source/target defaults | PASS |
| TC_014 | UI | Static | HTML local | có 2 select source/target | PASS |
| TC_015 | Font | Integration | fontconfig container | Noto Sans Thai/CJK tồn tại | PASS |
| TC_016 | Full regression | Regression | toàn bộ unittest | không có regression feature | BLOCKED bởi lỗi/missing dependency có sẵn ở host; chạy chọn lọc PASS |
