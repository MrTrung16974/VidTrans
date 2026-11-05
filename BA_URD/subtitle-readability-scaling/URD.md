# URD — Tối ưu khả năng đọc phụ đề

- Feature: `subtitle-readability-scaling`
- Version: `0.1`
- Trạng thái: `Draft`
- Ngày: 2026-11-05

## I. Thông tin chung

Mục tiêu là ngăn phụ đề dịch bị quá nhỏ trên video độ phân giải cao, đặc biệt video dọc. Phạm vi chỉ thay đổi phép tính layout; không đổi API, DB, OCR, dịch thuật hoặc quy trình đăng mạng xã hội.

Nguồn: yêu cầu người dùng; baseline production job `4037ce00`; `backend/main.py::DEFAULT_SUB_STYLE`; `backend/pipeline/subtitle_layout.py`.

## II. Tổng quan

AS-IS: `font_size` được scale từ chuẩn 720p nhưng `min_font_size`/`max_font_size` là pixel tuyệt đối. Job production `1080×1920` vì thế có ba cue đều ở 22px.

TO-BE: coi `font_size`, `min_font_size` và `max_font_size` là giá trị tại độ cao chuẩn 720p; scale cả ba theo chiều cao video trước khi fit/wrap.

## III. Yêu cầu chi tiết

### UC_01 — Render phụ đề dễ đọc theo độ phân giải

| Thuộc tính | Nội dung |
|---|---|
| Actor | Người tạo video |
| Trigger | Pipeline layout cue dịch |
| Pre-condition | Kích thước video hợp lệ; cue có hoặc không có bbox OCR |
| Post-condition | Cue có cỡ chữ tỷ lệ với chiều cao video và không tràn vùng an toàn |

| BR | Quy tắc |
|---|---|
| BR01_01 | `font_size`, `min_font_size`, `max_font_size` được hiểu tại chuẩn 720p và scale theo `video_height / 720`. |
| BR01_02 | Cỡ OCR sau matching phải clamp bằng sàn/trần đã scale. |
| BR01_03 | Cue dài được giảm tới sàn đã scale, sau đó wrap tối đa 3 dòng thay vì giảm dưới sàn. |
| BR01_04 | Ở 720p, kết quả tương thích hành vi hiện tại. |

| AC | Given | When | Then |
|---|---|---|---|
| AC01_01 | Video 1080×1920, min 22@720p, OCR source nhỏ | Layout cue | Cỡ chữ không nhỏ hơn 59px. |
| AC01_02 | Video cao 720px | Layout cue | Sàn vẫn là 22px. |
| AC01_03 | Cue dài | Fit/wrap | Không tràn ngang, không mất chữ, tối đa 3 dòng. |
| AC01_04 | Job production gần nhất | Render sau deploy | Không còn override `fs22`; frame đại diện dễ đọc hơn. |

## IV. NFR và phụ lục

- NFR_01: thay đổi thuần tính toán, không thêm I/O hay dependency.
- NFR_02: không đọc/ghi dữ liệu tenant khác ngoài job được người dùng yêu cầu kiểm tra.
- API/DB/Kafka/Camunda/SQL: N/A — không đổi contract hoặc persistence.

