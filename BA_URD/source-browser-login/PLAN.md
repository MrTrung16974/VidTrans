# Kế hoạch — Source browser login 1.0

Trạng thái: APPROVED BY USER REQUEST (“OKi bắt đầu làm” trong phiên 2026-10-15), phạm vi triển khai đầy đủ.

| Bước | Việc làm | Đầu ra | Kiểm chứng | Trạng thái |
|---|---|---|---|---|
| BA | Chốt AS-IS/TO-BE, AC, bảo mật | `ANALYSIS.md`, `URD.md` | tự review traceability | DONE |
| DEV | Thiết kế manager chung, API, downloader provider, sidecar, UI tabs | `DESIGN.md`, source/tests | unit/static/compose tests | DONE |
| TEST | Testcase và báo cáo thực thi | `TESTCASES.md`, `TEST_REPORT.md` | bằng chứng lệnh test | DONE (UAT pending) |
| DEPLOY | Manifest/runbook, không có SQL/Camunda | `MANIFEST.md`, `RUNBOOK.md` | compose config | DONE |

## Rủi ro/rollback

- Tốn RAM: mỗi sidecar có volume riêng và có thể tắt độc lập; rollback bằng bỏ hai service/routes mới.
- Cookie nhạy cảm: lọc theo domain, file mode 0600, không serialize ra API.
- Giữ tương thích `/api/v1/douyin-*` và `#douyin` để bookmark cũ tiếp tục hoạt động.
