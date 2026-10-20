# OCR throughput — test cases

| ID | Trường hợp | Kỳ vọng | Kết quả |
|---|---|---|---|
| TC_OCR_PERF_001 | TSV Tesseract có nhiều page và một page rỗng | Kết quả giữ đúng thứ tự input, page rỗng không làm lệch frame | PASS |
| TC_OCR_PERF_002 | Ba frame giống nhau | Chỉ inference một frame, cả ba observation giữ cùng nội dung và timestamp riêng | PASS |
| TC_OCR_PERF_003 | Tesseract thật đọc ba ảnh qua image-list | Trả đúng ba page theo thứ tự | PASS |
| TC_OCR_PERF_004 | Benchmark 64 ảnh trong image runtime | Batch nhanh hơn từng process, không thiếu page | PASS — 4.851s → 0.493s, 9.84x |
| TC_OCR_PERF_005 | Regression OCR/domain/checkpoint | Không phá contract hiện hữu | PASS — 39/39 |
| TC_OCR_PERF_006 | Ba frame cùng nét chữ, nền trơn đổi sáng | Chỉ gọi OCR một lần | PASS |
| TC_OCR_PERF_007 | Hình học subtitle đổi vị trí | Change gate chọn cả hai frame | PASS |
| TC_OCR_PERF_008 | Upscale 2× | Reader nhận ảnh 2×, ảnh nguồn không bị sửa | PASS |
| TC_OCR_PERF_009 | Regression sau lát tối ưu thứ hai | OCR/domain/checkpoint không lỗi | PASS — 42/42 |
| TC_OCR_PERF_010 | Benchmark video người dùng 60 giây | Ghi số candidate và thời gian | BLOCKED — decode CPU vượt cửa sổ benchmark |
