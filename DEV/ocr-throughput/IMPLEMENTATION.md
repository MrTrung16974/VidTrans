# OCR throughput — implementation

- `TesseractOCRReader.read_many()` dùng image-list của Tesseract, gom tối đa 32 frame cho mỗi
  lần gọi từ pipeline và tách TSV theo `page_num`.
- Frame gần như giống frame inference gần nhất tiếp tục tái sử dụng OCR, nhưng nay bước phát
  hiện thay đổi chạy trước khi lập batch.
- Tesseract được giới hạn ở 3 fps hiệu dụng; Paddle/custom reader không bị thay đổi.
- Progress tính theo frame thực sự inference; transcript checkpoint tăng từ version 4 lên 5.
- Benchmark cô lập trong image hiện tại trên 64 ảnh: 4.851 giây xuống 0.493 giây (9.84x).

## Lát tối ưu thứ hai

- FFmpeg chỉ trích ROI ở kích thước gốc. Pipeline upscale theo batch đúng những frame được đưa
  vào reader rồi xóa ngay file inference tạm; lượng pixel ghi cho tập frame ban đầu giảm 75% khi
  cấu hình upscale là 2×.
- Change gate dùng mask nét sáng có tương phản cục bộ ở chiều rộng tối đa 320 px, thay vì mean
  difference toàn khung. Nền trơn đổi sáng không còn làm OCR lại.
- Refresh cưỡng bức sau 1,5 giây giữ safety net cho hai caption có hình học gần giống nhau.
- Transcript checkpoint tăng version 5 lên 6.
- Thêm script benchmark an toàn `backend/scripts/benchmark_ocr_gate.py`; phép đo video dài bị
  giới hạn bởi tốc độ decode CPU của runtime và chưa được dùng làm bằng chứng PASS.

Con số video thực tế còn phụ thuộc mức chuyển động trong vùng subtitle và độ phân giải. Job đang
chạy phải hủy/chạy lại sau khi deploy vì process cũ không thể đổi thuật toán giữa chừng.
