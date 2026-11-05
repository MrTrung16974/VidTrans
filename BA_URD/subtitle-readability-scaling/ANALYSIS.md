# Phân tích — Tối ưu khả năng đọc phụ đề

- Feature: `subtitle-readability-scaling`
- Ngày: 2026-11-05
- Trạng thái: `DRAFT`

## Vấn đề và mục tiêu

- `CONFIRMED`: Người dùng phản ánh chữ trong video dịch gần nhất trên VPS quá nhỏ so với phụ đề mong muốn.
- `CONFIRMED`: Pipeline đặt `font_size=36` ở độ cao chuẩn 720p, nhưng cue có bbox OCR mặc định bật `match_source_size`; cỡ chữ có thể lấy từ chiều cao chữ nguồn rồi tiếp tục bị `_fit_text` thu nhỏ tới `min_font_size=22`.
- `CONFIRMED`: `min_font_size` và `max_font_size` hiện là giới hạn pixel tuyệt đối, trong khi `font_size` và `margin_v` được scale theo chiều cao video. Vì vậy sàn 22px không giữ cùng tỷ lệ thị giác giữa 720p, 1080p và video dọc/độ phân giải cao.
- Mục tiêu: phụ đề dịch dễ đọc ổn định theo độ phân giải, vẫn nằm trong khung hình, tối đa 2 dòng bình thường/3 dòng khi bất khả kháng và không che quá mức nội dung.

## AS-IS / TO-BE

| Mục | AS-IS | TO-BE đề xuất |
|---|---|---|
| Cỡ mặc định | Scale theo `video_height / 720` | Giữ nguyên |
| Sàn/trần cỡ chữ | Pixel tuyệt đối 22/72 | Scale theo độ cao video rồi clamp an toàn |
| Bám cỡ chữ OCR | Có thể giữ cỡ nguồn rất nhỏ | Có sàn khả năng đọc theo tỷ lệ khung hình |
| Cue dài | Thu nhỏ tới sàn rồi wrap | Ưu tiên wrap/mở rộng vùng chữ trước khi giảm dưới sàn khả năng đọc |
| Bằng chứng production | Chưa lấy được job VPS gần nhất | Đo kích thước video, style request và phân bố `subtitle_layout.font_size` của job gần nhất |

## Phạm vi

- Trong phạm vi: kiểm tra read-only job hoàn thành gần nhất trên VPS; sửa thuật toán layout/cấu hình mặc định; unit test; rebuild/restart dịch vụ; render lại hoặc retry job gần nhất nếu dữ liệu nguồn còn tồn tại; kiểm tra frame đại diện.
- Ngoài phạm vi: thay font/branding tổng thể, subtitle editor, thay OCR/ASR/translation, DB migration, Camunda.

## Hệ thống ảnh hưởng

- `backend/pipeline/subtitle_layout.py`
- `backend/main.py::DEFAULT_SUB_STYLE`
- `backend/tests/test_subtitle_layout.py`
- Render ASS, drawtext và Pillow cùng tiêu thụ `subtitle_layout.font_size`, nên không cần ba thuật toán cỡ chữ riêng.

## Nguồn bằng chứng

| ID | Nguồn | Kết quả |
|---|---|---|
| SRC_01 | Yêu cầu người dùng 2026-11-05 | Video dịch gần nhất có chữ quá nhỏ |
| SRC_02 | `backend/main.py::DEFAULT_SUB_STYLE` | Mặc định 36px; match source; min 22px; max 72px |
| SRC_03 | `backend/pipeline/subtitle_layout.py` | Default scale theo chiều cao; min/max không scale; OCR có thể quyết định cỡ cue |
| SRC_04 | VPS job `4037ce00` | 1080×1920; ba cue đều bị override `fs22`; frame xác nhận chữ Việt rất nhỏ giữa subtitle Trung/Anh |

## Giả định, câu hỏi và tiêu chí nghiệm thu

- `ASSUMPTION A01`: VPS dùng source checkout và Docker Compose như runbook hiện tại.
- `RESOLVED Q01`: VPS là `root@169.58.116.223`; người dùng đã cấp SSH key.
- `RESOLVED Q02`: đã render preview tách biệt từ source job, không retry/publish và không ghi đè output chính thức.
- AC01: ở 720p, hành vi mặc định không nhỏ hơn sàn đã chốt.
- AC02: ở 1080p/1440p/video dọc, sàn cỡ chữ tăng tương ứng thay vì giữ 22px.
- AC03: cue dài không tràn ngang khung; layout có tối đa 3 dòng fallback.
- AC04: job production gần nhất sau render có phân bố cỡ chữ đạt sàn theo độ phân giải và frame đại diện đọc rõ.
- AC05: không tự động đăng lại video lên nền tảng xã hội.

