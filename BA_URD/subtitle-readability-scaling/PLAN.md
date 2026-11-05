# Kế hoạch — Tối ưu khả năng đọc phụ đề

- Feature: `subtitle-readability-scaling`
- Version: `0.1`
- Trạng thái: `EXECUTED`
- Nguồn yêu cầu: người dùng, 2026-11-05
- Phạm vi: kiểm tra VPS → thiết kế/sửa layout → unit/regression test → deploy → xác minh job gần nhất.

## Kết quả mong muốn và Definition of Done

- Có số đo thực tế từ job hoàn thành gần nhất: kích thước video, style request, min/median/max font cue.
- Quy tắc sàn cỡ chữ scale theo độ phân giải và được bảo vệ bằng unit test.
- Regression layout/render liên quan chạy PASS.
- VPS được rebuild/restart an toàn, health check PASS.
- Job gần nhất được retry/render lại khi còn source và không kích hoạt publish; frame đại diện được kiểm tra trực quan.
- Có runbook/rollback ngắn và hồ sơ kết quả; không có DB/SQL/Camunda.

## Các bước

| Bước | Đầu vào | Việc làm | File đầu ra | Cách kiểm chứng | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| 1. Production baseline | SSH VPS | Tìm job hoàn thành gần nhất; đọc metadata không nhạy cảm; ffprobe output; thống kê layout font | Cập nhật `ANALYSIS.md` | Lệnh read-only + số đo | Host/SSH alias | DONE |
| 2. BA/Design | Baseline + source hiện tại | Chốt sàn scale, compatibility và rollback | `URD.md`, `DEV/.../DESIGN.md` | Self-review, trace AC | Bước 1 | DONE |
| 3. Code | Design đã review | Sửa scale min/max hoặc readability floor, giữ renderer contract | Source + `IMPLEMENTATION.md` | Code review | Bước 2 | DONE |
| 4. Test | Code | Unit test 720p/1080p, OCR chữ nhỏ, cue dài; regression | `TESTCASES.md`, `TEST_REPORT.md`, `TRACEABILITY.md` | Test thực thi | Bước 3 | DONE |
| 5. Deploy | Test PASS | Cập nhật VPS, rebuild/restart, smoke check | `DEPLOY/.../RUNBOOK.md`, `MANIFEST.md` | Container healthy/log không lỗi mới | SSH + quyền deploy | DONE |
| 6. Production verify | Deploy healthy | Render preview job gần nhất; kiểm tra font và frame | `TEST_REPORT.md`, `HANDOFF.md` | Preview + visual QA | Source job còn tồn tại | DONE |

## Rủi ro và rollback

- Chữ lớn hơn có thể che nội dung hoặc tăng số dòng: test cue dài và giới hạn vùng chữ trước deploy.
- Job cũ có style lưu sẵn: cần xác định merge default/request trên production để không sửa mà không có hiệu lực.
- Retry có thể kích hoạt workflow publish: dùng đường render/retry đã vô hiệu publish hoặc dừng ở smoke job nếu không chứng minh được an toàn.
- Rollback: checkout/redeploy revision trước; artifact cũ được giữ nguyên, không ghi đè.

## Tự review plan

- Nguồn/source path và renderer consumer đã xác minh.
- Không cần đọc secret; chỉ dùng SSH đã cấu hình và metadata job đã masking.
- Có testability, production baseline và rollback.
- `BLOCKER`: chưa có host/SSH alias hợp lệ; alias `vps` đã thử nhưng DNS không phân giải.
- Thay đổi business/DB/API: không có.

## Approval record

| Version/phạm vi | Người duyệt | Thời điểm | Xác nhận |
|---|---|---|---|
| 0.1 |  |  |  |

