# OCR throughput — implementation

- `TesseractOCRReader.read_many()` dùng image-list của Tesseract, gom tối đa 32 frame cho mỗi
  lần gọi từ pipeline và tách TSV theo `page_num`.
- Frame gần như giống frame inference gần nhất tiếp tục tái sử dụng OCR, nhưng nay bước phát
  hiện thay đổi chạy trước khi lập batch.
- Tesseract được giới hạn ở 3 fps hiệu dụng; Paddle/custom reader không bị thay đổi.
- Progress tính theo frame thực sự inference; transcript checkpoint tăng từ version 4 lên 5.
- Benchmark cô lập trong image hiện tại trên 64 ảnh: 4.851 giây xuống 0.493 giây (9.84x).

Con số video thực tế còn phụ thuộc mức chuyển động trong vùng subtitle và độ phân giải. Job đang
chạy phải hủy/chạy lại sau khi deploy vì process cũ không thể đổi thuật toán giữa chừng.
