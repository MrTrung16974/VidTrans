# DESIGN — n8n workflow events cho upload, download, dịch và publish

- Plan: `BA_URD/multi-tenant-commercial-platform/PLAN.md` version 0.2, APPROVED.
- URD: bỏ qua theo chỉ đạo người dùng ngày 2026-10-10.
- Phạm vi lát triển khai này: tích hợp n8n vào pipeline hiện hữu; chưa triển khai toàn bộ multi-tenant/Admin/billing.

## 1. Mục tiêu

Phát sự kiện bền vững tại các mốc upload, download nguồn, dịch, hoàn tất/thất bại và upload TikTok để n8n thực hiện thông báo, CRM, theo dõi hoặc workflow phụ. Pipeline video không chờ n8n và không truyền binary/path/URL nguồn/secret qua webhook.

## 2. Điểm mở rộng đã xác minh

- `backend/main.py::update_job`: ranh giới cập nhật trạng thái của pipeline.
- `backend/main.py::process_video_endpoint/create_batch_endpoint`: thời điểm upload file đã validate và job đã tạo.
- `backend/main.py::prepare_job_source_video`: download nguồn và chuyển `source-ready`.
- `backend/main.py::process_video`: chuyển `translating`, tạo translation artifact, hoàn tất/thất bại.
- `backend/main.py::publish_scheduled_tiktok`: upload TikTok và reconcile trạng thái job.
- `backend/work/jobs.sqlite3`: persistence hiện hữu; outbox dùng bảng riêng cùng DB nhưng repository độc lập.

## 3. Thiết kế

### Durable outbox

`WorkflowEventStore` ghi sự kiện vào `workflow_events` trước khi giao. Mỗi event có UUID, loại, aggregate/job ID, payload JSON, số lần thử, `available_at`, trạng thái và lỗi cuối đã rút gọn. Enqueue không gọi mạng.

`N8nWorkflowDispatcher` poll event đến hạn, POST JSON sang một webhook n8n, đánh dấu delivered khi nhận HTTP 2xx; lỗi mạng/5xx được retry exponential có giới hạn. Lỗi không làm fail media job.

### Event contract version 1

```json
{
  "version": 1,
  "event_id": "uuid",
  "event_type": "translation.completed",
  "occurred_at": "UTC ISO-8601",
  "job": {
    "job_id": "...",
    "batch_id": "...",
    "status": "processing",
    "step": "translation-ready",
    "progress": 0.46,
    "source_platform": "douyin",
    "source_language": "zh-CN",
    "target_language": "vi",
    "review_cues": 0,
    "translation_fallback_cues": 0,
    "tiktok_publish_status": null
  }
}
```

Allow-list payload không có filename, local path, source URL, cookie, transcript, bản dịch, token hay nội dung video.

### Event types

- `source.uploaded`
- `source.ready`
- `download.started`, `download.completed`
- `translation.started`, `translation.completed`
- `publish.started`
- `job.completed`, `job.failed`, `job.cancelled`

### Security/config

- `VIDTRANS_N8N_WEBHOOK_URL`: URL webhook; bỏ trống thì integration tắt.
- `VIDTRANS_N8N_WEBHOOK_SECRET`: bắt buộc khi URL được cấu hình; dùng HMAC-SHA256 trên raw body.
- `VIDTRANS_N8N_TIMEOUT_SECONDS`: timeout 1–30 giây, mặc định 5.
- `VIDTRANS_N8N_MAX_ATTEMPTS`: 1–20, mặc định 8.
- HTTP chỉ được dùng với localhost; host khác bắt buộc HTTPS.
- Header: `X-VidTrans-Event-Id`, `X-VidTrans-Timestamp`, `X-VidTrans-Signature: sha256=<hex>`.

## 4. Idempotency/retry

- `event_id` là idempotency key phía n8n; workflow phải lưu/kiểm tra ID trước side effect.
- Dispatcher claim atomically để tránh giao song song trong một SQLite DB.
- Retry backoff tối đa 5 phút; hết số lần thử chuyển `dead` để kiểm tra thủ công.
- Timeout có thể tạo kết quả không xác định, nên delivery là at-least-once; không tuyên bố exactly-once.

## 5. Test strategy

- Store: enqueue, claim, delivered, retry/dead, recover event đang gửi sau restart.
- Contract: payload allow-list không lộ URL/path/secret/nội dung.
- Client: chữ ký HMAC đúng; 2xx delivered; lỗi dependency retry mà không ảnh hưởng job.
- Transition mapper: không phát event cho mỗi progress update; chỉ phát khi status/step đổi sang mốc quan trọng.

## 6. Tradeoff

- Dùng SQLite phù hợp deployment hiện tại; khi chuyển PostgreSQL, giữ interface store và dùng `FOR UPDATE SKIP LOCKED`.
- Một webhook chung giúp cấu hình đơn giản; n8n route theo `event_type`.
- n8n không quyết định state pipeline. Nếu n8n down, VidTrans vẫn xử lý và outbox giao lại sau.
