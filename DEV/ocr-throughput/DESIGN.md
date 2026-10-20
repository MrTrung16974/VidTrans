# OCR throughput — thiết kế

## Vấn đề

Tesseract hiện được gọi bằng một process mới cho từng frame. Video 15 phút ở 5 fps tạo khoảng
4.500 lần khởi động process, khiến thời gian OCR chủ yếu là overhead thay vì nhận dạng.

## Hướng xử lý

1. Tesseract nhận danh sách ảnh và xử lý theo lô 32 frame/process; TSV được tách lại theo
   `page_num` để giữ đúng kết quả từng frame.
2. Giữ cổng phát hiện frame gần như không đổi trước inference; frame bỏ qua tái sử dụng kết quả
   gần nhất để không làm đứt cue.
3. Với backend Tesseract, giới hạn nhịp quét hiệu dụng ở 3 fps. Mốc này vẫn cho ít nhất hai mẫu
   với caption tồn tại khoảng 0,67 giây, đồng thời giảm 40% số frame so với cấu hình cũ 5 fps.
4. Tiến độ OCR phản ánh số frame thực sự phải inference, không phải toàn bộ frame đã trích.
5. Tăng version transcript checkpoint để không dùng lại kết quả OCR theo thuật toán cũ.

## Lát tối ưu thứ hai

6. Trích ROI ở kích thước gốc thay vì upscale toàn bộ hàng nghìn frame. Chỉ những frame vượt
   change gate mới được upscale ngay trước inference, sau đó xóa ảnh inference tạm theo batch.
7. Change gate không so sánh độ sáng toàn khung. Hệ thống tạo signature kích thước nhỏ từ cạnh
   tương phản sáng — đặc trưng của chữ subtitle trắng có viền — rồi so XOR/union giữa hai
   signature. Chuyển động hoặc đổi sáng nền trơn vì vậy không kích hoạt OCR lại.
8. Dù signature không đổi, hệ thống buộc refresh sau tối đa 1,5 giây để giới hạn rủi ro bỏ sót
   thay đổi chữ có hình học gần giống nhau.
9. Không crop động theo bbox ở lát này: bbox chỉ có sau inference và crop sai có thể cắt mất
   subtitle chuyển vị trí. Không dùng quét 1 fps mặc định vì caption ngắn có thể thiếu hai mẫu
   consensus.

## An toàn và tương thích

- PaddleOCR và reader test/custom tiếp tục dùng API `read()` hiện hữu.
- Nếu batch Tesseract lỗi, job fail với stderr rút gọn như trước; không ghi nội dung video vào log.
- Không thay schema database và không thay quy tắc hợp nhất OCR/ASR.

## Kiểm thử

- Parse TSV nhiều page, kể cả page không có chữ.
- Xác nhận một batch dùng một process và ánh xạ đúng frame.
- Xác nhận frame giống nhau không gọi OCR lại nhưng vẫn tạo đủ observation/timestamp.
- Xác nhận nền đổi sáng nhưng chữ giữ nguyên không gọi OCR lại; chữ xuất hiện hoặc đổi hình học
  vẫn được chọn.
- Xác nhận ảnh trích giữ kích thước gốc còn ảnh đưa vào reader đúng hệ số upscale.
- Regression OCR, domain request và checkpoint.
