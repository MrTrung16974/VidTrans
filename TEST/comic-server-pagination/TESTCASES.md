# Test cases

| ID | Nhóm | Bước | Kết quả mong đợi | Trạng thái |
|---|---|---|---|---|
| TC_001 | API/P1 | Gọi projects trang 1 và 2, limit 2 | Mỗi trang trả đúng lát và cùng total | NOT_RUN |
| TC_002 | Filter/P1 | Trộn comic/non-comic rồi gọi API | Chỉ comic xuất hiện | NOT_RUN |
| TC_003 | Boundary/P1 | Gọi page vượt total_pages | Backend trả trang cuối hợp lệ | NOT_RUN |
| TC_004 | Scene/P1 | Gọi scene_page 1 và 2 | Storyboard chỉ chứa scene của trang; total giữ nguyên | NOT_RUN |
| TC_005 | Auth/P1 | User thường gọi danh sách | Chỉ dữ liệu thuộc user đó | NOT_RUN |
| TC_006 | UI/P2 | Đổi trang/kích thước trang | Có request backend mới; danh sách và metadata cập nhật | NOT_RUN |

