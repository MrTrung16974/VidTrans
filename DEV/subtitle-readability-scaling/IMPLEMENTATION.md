# Implementation — Subtitle readability scaling

- Ngày: 2026-11-05
- Source: `backend/pipeline/subtitle_layout.py`
- Test: `backend/tests/test_subtitle_layout.py`

## Thay đổi

- Scale `min_font_size` và `max_font_size` theo `video_height / 720`, đồng nhất với `font_size`/`margin_v` hiện hữu.
- Clamp cỡ OCR và sàn `_fit_text` bằng giới hạn đã scale.
- Thêm regression 1080×1920 (59px) và compatibility 720p (22px).

## Kiểm chứng

- Local `cd backend && python3 -m unittest tests.test_subtitle_layout`: 11/11 PASS.
- Local regression mở rộng: layout PASS; `test_audio_duration` và `test_clip_split` BLOCKED khi import do host thiếu package `cryptography`, không phải assertion failure.
- Production container `docker exec vidtrans python -m unittest tests.test_subtitle_layout`: 11/11 PASS.
- Production calculation cho baseline 1080×1920: 59px.
- Preview từ video nguồn job `4037ce00` đã render bằng 59px và kiểm tra trực quan: chữ Việt rõ hơn đáng kể.

