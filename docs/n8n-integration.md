# Tích hợp n8n với VidTrans

## Mục đích

VidTrans phát webhook khi pipeline đi qua các mốc upload, download, dịch, đăng TikTok và hoàn tất/thất bại. n8n dùng các event này cho thông báo, CRM, báo cáo hoặc workflow phụ; n8n không nhận file video và không điều khiển trạng thái gốc của job.

## Cấu hình

Khai báo bằng secret/environment của môi trường triển khai, không commit giá trị thật:

```text
VIDTRANS_N8N_WEBHOOK_URL=https://n8n.example.com/webhook/vidtrans
VIDTRANS_N8N_WEBHOOK_SECRET=<shared-secret>
VIDTRANS_N8N_TIMEOUT_SECONDS=5
VIDTRANS_N8N_MAX_ATTEMPTS=8
```

Nếu không khai báo URL, integration tắt và VidTrans không tạo event outbox. URL ngoài localhost bắt buộc HTTPS. Khi URL được bật, secret là bắt buộc.

VidTrans đồng thời gửi `Authorization: Bearer <secret>` để dùng trực tiếp với Header Auth của Webhook node. Với Docker dev nội bộ, xem [`deploy/n8n/README.md`](../deploy/n8n/README.md); chỉ môi trường này mới được bật `VIDTRANS_N8N_ALLOW_INSECURE_HTTP=1`.

## Workflow n8n đề xuất

```text
Webhook POST /vidtrans
  -> Verify HMAC + timestamp
  -> Deduplicate bằng event_id
  -> Switch theo event_type
       source.uploaded/download.completed -> cập nhật dashboard vận hành
       translation.completed              -> thông báo bản dịch sẵn sàng
       publish.started                     -> theo dõi đăng TikTok
       job.completed                       -> email/Zalo/Telegram + CRM
       job.failed                          -> cảnh báo hỗ trợ
```

Không cấu hình workflow để tải binary về n8n. Khi cần cung cấp link kết quả cho user, n8n gọi một API VidTrans được xác thực để yêu cầu signed URL ngắn hạn; API này sẽ được bổ sung cùng multi-tenant/object storage.

## Xác minh webhook

VidTrans gửi:

- `X-VidTrans-Event-Id`: UUID dùng chống xử lý trùng.
- `X-VidTrans-Timestamp`: Unix timestamp lúc gửi.
- `X-VidTrans-Signature`: `sha256=<HMAC-SHA256(raw_body, secret)>`.

Workflow phải:

1. Lấy raw request body, không stringify lại JSON trước khi kiểm tra.
2. Từ chối timestamp lệch quá giới hạn vận hành, đề xuất 5 phút.
3. Tính HMAC-SHA256 bằng shared secret và so sánh constant-time.
4. Lưu `event_id` trước side effect; event đã xử lý phải trả 2xx nhưng không chạy lại.
5. Trả 2xx nhanh, đưa tác vụ chậm sang workflow con/queue.

## Event contract

Payload version 1 chỉ chứa metadata allow-list:

```json
{
  "version": 1,
  "event_id": "f9a4...",
  "event_type": "translation.completed",
  "occurred_at": "2026-10-10T10:00:00+00:00",
  "job": {
    "job_id": "abc123",
    "batch_id": "batch_xyz",
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

Không có filename, local path, URL nguồn, cookie, token, transcript, nội dung bản dịch hoặc video.

## Delivery và sự cố

- Giao theo nguyên tắc at-least-once; n8n bắt buộc idempotent theo `event_id`.
- HTTP 2xx: event được đánh dấu `delivered`.
- Timeout/lỗi mạng/non-2xx: retry exponential, tối đa theo cấu hình.
- Hết số lần thử: event chuyển `dead`; media job vẫn hoàn thành bình thường.
- Event ở trạng thái `sending` khi server dừng được đưa lại `pending` lúc khởi động.

## Event đang hỗ trợ

- `source.uploaded`, `source.ready`
- `download.started`, `download.completed`
- `translation.started`, `translation.completed`
- `publish.started`
- `job.completed`, `job.failed`, `job.cancelled`
