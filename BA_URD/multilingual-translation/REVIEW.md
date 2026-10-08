# Review

| ID | Finding | Mức độ | Xử lý | Trạng thái |
|---|---|---|---|---|
| RV01 | Validation `contains_han` không dùng được cho đích Trung/Nhật | High | validator xét target; test Trung/Nhật và output Trung còn sót | CLOSED |
| RV02 | TTS/ASR/provider hardcode locale | High | catalog + adapter | CLOSED |
| RV03 | Job legacy thiếu field mới | High | defaults tại read/resume + unit test config cũ | CLOSED |
| RV04 | OCR chỉ Trung nhưng UI có nguồn đa ngôn ngữ | Medium | ràng buộc API/UI | CLOSED |
