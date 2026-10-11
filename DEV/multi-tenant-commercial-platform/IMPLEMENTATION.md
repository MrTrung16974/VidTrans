# IMPLEMENTATION — n8n workflow events

- Ngày: `2026-10-10`
- Phạm vi: lát tích hợp n8n cho upload/download/dịch/publish; chưa gồm multi-tenant/Admin/billing.

## Thay đổi

- Thêm `backend/infrastructure/workflow_events.py`:
  - SQLite durable outbox dùng chung database path hiện hữu nhưng bảng riêng.
  - Event payload allow-list, không chứa binary/path/URL/cookie/token/nội dung.
  - HMAC-SHA256, HTTPS bắt buộc ngoài localhost.
  - Dispatcher nền, retry exponential, dead event và recovery sau restart.
- Nối `backend/main.py`:
  - Upload hợp lệ phát `source.uploaded`.
  - State transition phát event download, translation, publish và terminal job.
  - Thêm mốc `translation-ready` sau khi kiểm tra translation hoàn chỉnh.
  - Dispatcher start/stop cùng FastAPI.
- Docker Compose forward bốn biến cấu hình n8n.
- Thêm tài liệu `docs/n8n-integration.md` và config contract.
- Thêm unit test cho allow-list, transition, outbox, retry/dead, HTTPS và HMAC.
- Bổ sung Admin API để xem summary/list event và replay event `dead`; API không trả `payload_json`.
- Bổ sung tab Admin với metric pending/sending/delivered/dead, bộ lọc và nút gửi lại.
- Bổ sung stack n8n + PostgreSQL cho dev, bind UI vào localhost và nối Docker network VidTrans.
- Webhook gửi cả HMAC và Bearer Header Auth để tương thích Webhook node chuẩn; HTTP nội bộ cần opt-in dev rõ ràng.

## Kiểm chứng

| Lệnh | Working directory | Kết quả |
|---|---|---|
| `python3 -m unittest tests.test_workflow_events tests.test_job_scheduler tests.test_job_store tests.test_frontend_static` | `backend/` | PASS — 30 tests |
| `python3 -m py_compile main.py infrastructure/workflow_events.py` | `backend/` | PASS |
| `python3 -m unittest discover -s tests` | `backend/` | 214 tests chạy; các test được nạp qua, suite ERROR duy nhất vì module `infrastructure.douyin_qr_auth` đã thiếu trước phạm vi thay đổi |
| `node --input-type=module --check < frontend/js/app.js` | `backend/` | PASS |
| Local UI smoke tại `/#admin` | local Uvicorn | PASS — API summary/list HTTP 200, trạng thái disabled đúng, không có console error |
| `docker compose -f deploy/n8n/compose.dev.yml config --quiet` với secret synthetic | repository root | PASS |
| `git diff --check` | repository root | PASS |

Không gọi webhook thật, không dùng secret thật và không chạy pipeline media/network trong kiểm chứng này.

## Giới hạn

- Outbox đang dùng SQLite, phù hợp single deployment hiện tại; phải chuyển store sang PostgreSQL trước multi-instance production.
- Chưa có phân trang UI và thao tác bulk replay; API đã hỗ trợ limit/offset.
- n8n workflow thực tế và secret cần được cấu hình ở môi trường triển khai.
- Delivery là at-least-once; workflow n8n phải deduplicate theo `event_id`.
