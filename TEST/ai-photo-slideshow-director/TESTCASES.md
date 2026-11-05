# Testcases — AI Photo Slideshow Director

| TC ID | Phạm vi | Bước chính | Kết quả mong đợi | Trạng thái |
|---|---|---|---|---|
| TC_001 | Planner happy path | Model trả plan đủ ảnh | Giữ style/order hợp lệ, normalize duration | PASS |
| TC_002 | Hallucination | Model trả track không tồn tại | Track bị loại | PASS |
| TC_003 | Invalid model | JSON lỗi/index sai/model exception | Trả heuristic plan | PASS |
| TC_004 | Keep order | Model đảo ảnh, user bật giữ thứ tự | Order trở về 0..n-1 | PASS |
| TC_005 | Authz | User khác gọi ai-plan | 404 | NOT_RUN |
| TC_006 | Web | Phân tích → xem plan → nghe thử → chọn nhạc | UI đúng, chỉ một audio | NOT_RUN |
| TC_007 | Web error | Endpoint lỗi | Toast, manual flow vẫn dùng được | NOT_RUN |
| TC_008 | App | Phân tích/apply/nghe thử | UI và audio lifecycle đúng | NOT_RUN |
| TC_009 | App analyze | Chạy analyzer | Không lỗi | PASS |
| TC_010 | Submit | Plan bị sửa enum/index | 400 trước reserve quota | NOT_RUN |
| TC_011 | Renderer graph/runtime | 2 slide tổng hợp, pan + crossfade | Graph allowlist và FFmpeg tạo MP4 | PASS |
| TC_012 | Real render E2E | Ảnh ngang/dọc qua compose + audio | MP4 đúng duration, phát được | NOT_RUN |
| TC_013 | Long duration | Duration > n×8 | Lặp sequence, mỗi slide trong biên | PASS (planner unit) |
| TC_014 | Regression | Không gửi plan | Dùng renderer timed cũ | PASS (unit) |

