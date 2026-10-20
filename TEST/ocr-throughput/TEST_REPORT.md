# OCR throughput — test report

Ngày chạy: 2026-10-20  
Môi trường: Docker image `vidtrans-vidtrans`, dữ liệu unit tổng hợp.

## Kết quả

- `python -m unittest tests.test_ocr_pipeline tests.test_domain_models tests.test_stage_checkpoint`
  sau lát 2: **42/42 PASS**.
- Tesseract image-list thật: 3/3 page đúng thứ tự.
- Benchmark Tesseract 64 ảnh: từng process 4.851s, batch 0.493s, nhanh hơn 9.84x.
- Full backend trước lát 2: 418 test chạy qua, 1 lỗi import test Douyin đã bị xoá, không liên quan OCR.
- Benchmark video thật 60 giây và bản rút gọn bị dừng vì decode trong Docker CPU vượt cửa sổ;
  trạng thái **BLOCKED**, không ghi nhận số liệu suy đoán.

## Giới hạn

- Chưa đo cue recall/CER trên golden corpus.
- Chưa deploy vì build bị chặn bởi CA proxy và PostgreSQL Compose hiện thiếu nguồn dữ liệu.
- Unit test không thay thế UAT video thật sau deploy.

## Cập nhật 2026-10-20 — bỏ upscale cho frame rộng, model mobile

Benchmark CPU (image `vidtrans-vidtrans`, 24 strip 1080×302 chữ Trung tổng hợp, nền nhiễu):

| Cấu hình | ms/frame | Độ chính xác |
|---|---|---|
| v6_medium + upscale 2× (cũ) | 4992 | 24/24 |
| v6_medium, không upscale (mặc định mới cho frame ≥1000px) | 1580 | 24/24 |
| PP-OCRv5_mobile, không upscale (`VIDTRANS_OCR_MODEL=mobile`) | 273 | 20/24, similarity 0.982 |

- Smoke `scan_subtitle_frames` với reader Paddle thật: medium 1326 ms, mobile 248 ms/frame; 3/3 câu đúng.
- Full backend: 447 test, 446 PASS, 1 lỗi import `test_douyin_qr_auth` có sẵn.
- Chưa đo trên video thật.
