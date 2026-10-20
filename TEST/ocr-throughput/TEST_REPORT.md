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
