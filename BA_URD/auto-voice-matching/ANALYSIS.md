# Phân tích — Tối ưu khớp giọng tự động

- `CONFIRMED`: pipeline hiện phân loại từng subtitle cue bằng F0 và kế thừa giọng gần nhất khi thiếu tín hiệu.
- `CONFIRMED`: cue ngắn, khoảng lặng và nhiễu có thể tạo kết quả không ổn định hoặc đổi giọng đột ngột.
- `ASSUMPTION`: “khớp giọng” trong phạm vi này là chọn kiểu giọng TTS nam/nữ gần giọng nguồn, không phải clone danh tính người nói.
- `OPEN`: chưa có tập audio người dùng đã gắn nhãn để đo độ chính xác thực tế.

## Mục tiêu

- Phân tích toàn bộ cue trước khi gán giọng.
- Không đổi giọng vì một điểm yếu nằm giữa hai cue cùng kiểu giọng.
- Cue không đủ tín hiệu dùng bằng chứng lân cận hoặc giọng chủ đạo toàn video trước fallback người dùng.
- Giữ tương thích API/form và không thêm dịch vụ ngoài.

## Ngoài phạm vi

- Voice cloning, nhận diện danh tính người nói, diarization bằng model nặng.
- Thay đổi nhà cung cấp Edge TTS/gTTS.

## Tiêu chí nghiệm thu

- Low/high pitch rõ vẫn chọn đúng kiểu giọng.
- Cue im lặng giữa hai cue cùng kiểu giọng được điền theo lân cận.
- Một outlier yếu không làm đổi giọng hai lần liên tiếp.
- Đoạn nhiều người nói rõ vẫn được phép chuyển giọng.
- Metadata giải thích nguồn quyết định và test hồi quy đều xanh.
