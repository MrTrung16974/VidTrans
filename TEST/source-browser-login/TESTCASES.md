# Test cases — Source browser login

| TC ID | AC/BR | Nhóm | Bước | Kết quả mong đợi | Trạng thái |
|---|---|---|---|---|---|
| TC_001 | AC01 | UI | Mở Đăng nhập nguồn, chuyển ba tab | Đúng panel/iframe/status | PASS (static) |
| TC_002 | BR01_01 | Security | Lọc bộ cookie trộn domain | Chỉ domain platform được export | PASS |
| TC_003 | BR01_02 | Functional | Sync YouTube có SAPISID | authenticated=true, file được tạo | PASS |
| TC_004 | BR01_02 | Exception | Sync Facebook thiếu `xs` | authenticated=false | PASS |
| TC_005 | BR02_02/03 | Integration | Tải YouTube với provider | yt-dlp nhận snapshot; snapshot bị xóa | PASS |
| TC_006 | NFR_01 | Security | Resolve iframe public URL | Chỉ local/proxy allowlist được nhận | PASS |
| TC_007 | AC06 | Regression | Chạy test manager/downloader Douyin | Không regression | PASS |
| TC_007A | BR01_01/04 | Security | Export/logout trong browser dùng chung | Chỉ cookie domain đang chọn bị đọc/xóa | PASS |
| TC_007B | AC01 | Runtime | Activate YouTube rồi Facebook | Một VNC điều hướng đúng trang, API available=true | PASS |
| TC_008 | AC02 | UAT | Đăng nhập thật YouTube/Facebook + Sync | Nhận diện đúng phiên | NOT_RUN |
| TC_009 | AC03 | UAT | Tải video thật sau Sync | Tải thành công với cookie tự cấp | NOT_RUN |
| TC_010 | AC04 | UAT | Logout một tab | Chỉ phiên tab đó bị xóa | NOT_RUN |
