# Implementation — Tối ưu khớp giọng tự động

## Đã triển khai

- Lọc pitch outlier bằng median/MAD trước khi phân loại.
- Confidence kết hợp voiced ratio, periodicity và độ ổn định pitch.
- Phân tích hai lượt trên toàn bộ cue thay vì kế thừa tuần tự.
- Cue ambiguous dùng hai hàng xóm đồng thuận, giọng chủ đạo hoặc fallback.
- Làm mượt điểm đổi giọng đơn lẻ có confidence thấp; bằng chứng mạnh vẫn giữ.
- Metadata có raw/resolved/global voice và nguồn quyết định.
- TTS tận dụng có giới hạn khoảng nghỉ thật trước câu kế tiếp, tránh tăng tốc không cần thiết.
- Tempo tự động tăng giới hạn nhẹ lên mức vẫn tự nhiên và có deadband chống xử lý dao động nhỏ.
- Artifact segment có target duration, overrun và fit quality để QA video thật.
- UI có 5 preset giọng: Tự nhiên, Ấm áp, Tươi sáng, Trầm, Năng động.
- Backend dùng allowlist preset, truyền rate/pitch vào Edge TTS và lưu tuning vào metadata từng segment.
- Resume tương thích job cũ bằng preset `natural` mặc định.

## Giới hạn

- Đây là chọn kiểu giọng TTS nam/nữ theo pitch, không phải voice cloning hoặc speaker identity.
- Chưa đo trên tập audio thực tế đã gắn nhãn; cần UAT với video của người dùng.
- Audio nhiều người nói chồng nhau vẫn có thể cần diarization/model chuyên dụng.
- Runtime `uv` local hiện chưa cài module `edge_tts` dù `requirements.txt` đã khai báo; kiểm thử provider thật chưa chạy. Khi thiếu module, pipeline giữ fallback gTTS nhưng gTTS không tái tạo đầy đủ rate/pitch của preset.

## Kiểm chứng

- Targeted voice/translation suite: 14 PASS.
- Voice + domain/language regression suite: 26 PASS.
- Sau tối ưu timing: voice + timing + translation + domain/language regression suite 28 PASS.
- Sau bổ sung preset: language/domain/voice/timing/translation suite 30 PASS.
