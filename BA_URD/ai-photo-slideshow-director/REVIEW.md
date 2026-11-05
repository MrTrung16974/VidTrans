# Review

| RV ID | Artifact | Finding | Mức độ | Xử lý | Trạng thái |
|---|---|---|---|---|---|
| RV01 | Planner | Model có thể bịa enum/index/track | Cao | Parser allowlist + exact image set + track lookup + fallback | CLOSED |
| RV02 | Submit API | Client có thể sửa plan snapshot | Cao | Parse/normalize lại theo ảnh và thư viện hiện tại | CLOSED |
| RV03 | Renderer | Không cho model truyền filter/path | Cao | Renderer tự sinh expression từ enum | CLOSED |
| RV04 | Backend integration tests | Host thiếu FastAPI/cryptography; Docker daemon tắt | Trung bình | Giữ test đã viết, ghi NOT_RUN; cần chạy trong image backend | OPEN |
| RV05 | UI | Chưa UAT trực quan trên web/app | Trung bình | Test thủ công theo TESTCASES trước rollout | OPEN |

