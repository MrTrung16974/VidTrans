# OCR throughput — deployment handoff

## Trạng thái

- Code và test đã hoàn tất.
- Lát tối ưu thứ hai đã thêm text-mask change gate, refresh 1,5 giây và upscale thích nghi;
  regression liên quan đạt 42/42.
- Chưa restart container `vidtrans`; container đang chạy vẫn dùng image cũ.
- `docker compose build vidtrans` ngày 2026-10-20 thất bại khi tải PyTorch do
  `SSL_CERT_VERIFY_FAILED` từ proxy/chứng chỉ nội bộ.
- Image hotfix không tải dependency đã build thành công với tag
  `vidtrans-vidtrans:ocr-throughput`; Dockerfile nằm cùng thư mục này. Chưa đổi tag Compose và
  chưa restart dịch vụ.
- PostgreSQL Compose hiện không có container và cũng không thấy volume `vidtrans_postgres-data`;
  log ứng dụng báo không resolve được host `postgres`.

## Điều kiện trước khi deploy

1. Cấp CA hợp lệ qua Docker build secret `corporate_ca`; không tắt TLS verification.
2. Xác định đúng nguồn/volume PostgreSQL hiện hữu trước khi chạy `docker compose up`; không tự
   tạo database trống khi chưa xác nhận dữ liệu.
3. Build lại image, chạy nhóm test OCR, sau đó restart `vidtrans` trong cửa sổ bảo trì.
4. Hủy/chạy lại job OCR cũ; job đang chạy không thể nhận thuật toán mới giữa chừng.

## Kiểm tra sau deploy

- Progress OCR phải hiển thị tổng frame inference thấp hơn tổng frame trích.
- Với Tesseract, log có dòng cap về 3 fps khi request lớn hơn 3 fps.
- Thư mục `ocr_frames` không được giữ file `ocr_input_*` sau khi batch hoàn tất hoặc lỗi.
- Chạy một video mẫu và so cue coverage với output cũ trước khi áp dụng rộng.
