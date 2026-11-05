# Test report — Subtitle readability scaling

- Ngày: 2026-11-05
- Baseline: job production `4037ce00`, 1080×1920, 3/3 cue ở 22px.
- Kết quả: 5 PASS, 0 FAIL.

## Bằng chứng

- Local unit: 11/11 PASS.
- Production container unit: 11/11 PASS trong 0,004 giây.
- Production calculation: `font_size=59` cho dữ liệu tương đương baseline.
- Preview 59px được render từ source video và xem tại ba cue; chữ Việt rõ hơn bản 22px.
- Smoke: `/api/v1/auth/status` trả cấu hình sẵn sàng; web nội bộ HTTP 200; HTTPS portal HTTP/2 200; log 2 phút sau deploy không có ERROR/Traceback/Exception.

## Giới hạn

- Chưa tạo một retry job mới qua UI/API; output cũ được giữ nguyên để không ghi đè artifact người dùng.
- Local regression `test_audio_duration`/`test_clip_split` BLOCKED do Python host thiếu `cryptography`; test layout trực tiếp và test trong image production đều PASS.

