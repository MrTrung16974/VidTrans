# Handoff — Dọn tính năng/table cũ

- Đã audit tĩnh reader/writer/routes/UI của các bảng chính.
- Không có table nào hoàn toàn vô chủ ở trạng thái code hiện tại.
- Candidate hợp nhất: TikTok direct/scheduled stack → social publishing stack; sau migration có thể drop `tiktok_publish_attempts`.
- Không được drop `attempts` của TikTok browser hoặc các bảng job/account/quota/audit/payment/workflow.
- Đã xoá OCR compatibility và stack TikTok direct/scheduled riêng; UI dùng social publish API, chỉ còn một scheduler/store chung.
- 87/87 regression PASS. PostgreSQL 17 disposable migrate/drop/postcheck/rollback PASS.
- Đã tạo SQL có backup guard và runbook. Chưa chạy trên production vì chưa xác minh database đích; production drop/UAT vẫn NOT_RUN.
