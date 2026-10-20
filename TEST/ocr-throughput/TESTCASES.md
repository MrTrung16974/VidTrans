# OCR throughput — test cases

| ID | Trường hợp | Kỳ vọng | Kết quả |
|---|---|---|---|
| TC_OCR_PERF_001 | TSV Tesseract có nhiều page và một page rỗng | Kết quả giữ đúng thứ tự input, page rỗng không làm lệch frame | PASS |
| TC_OCR_PERF_002 | Ba frame giống nhau | Chỉ inference một frame, cả ba observation giữ cùng nội dung và timestamp riêng | PASS |
| TC_OCR_PERF_003 | Tesseract thật đọc ba ảnh qua image-list | Trả đúng ba page theo thứ tự | PASS |
| TC_OCR_PERF_004 | Benchmark 64 ảnh trong image runtime | Batch nhanh hơn từng process, không thiếu page | PASS — 4.851s → 0.493s, 9.84x |
| TC_OCR_PERF_005 | Regression OCR/domain/checkpoint | Không phá contract hiện hữu | PASS — 39/39 |
