# n8n dev stack cho VidTrans

Stack này dành cho local/dev: n8n + PostgreSQL, UI chỉ bind `127.0.0.1:5678`. Production phải đặt n8n sau HTTPS/reverse proxy, secret manager và backup riêng.

## 1. Điều kiện

Khởi động stack VidTrans trước để có network mặc định:

```bash
docker compose up -d
```

Tên network mặc định là `vidtrans_default`. Nếu project Compose dùng tên khác, đặt `VIDTRANS_DOCKER_NETWORK` thành network thực tế.

## 2. Secret dev

Đặt ngoài Git:

```text
N8N_POSTGRES_PASSWORD=<random-dev-password>
N8N_ENCRYPTION_KEY=<random-long-encryption-key>
N8N_VERSION=<tested-n8n-version>
```

Không dùng giá trị dev cho production. Nên pin `N8N_VERSION` vào phiên bản đã test thay vì để mặc định major tag `2`.

## 3. Khởi động

```bash
docker compose -f deploy/n8n/compose.dev.yml up -d
```

Mở `http://127.0.0.1:5678`, tạo owner local và workflow Webhook production path `vidtrans`.

Trong Webhook node, dùng Header Auth:

```text
Header name: Authorization
Header value: Bearer <cùng VIDTRANS_N8N_WEBHOOK_SECRET>
```

Sau đó cấu hình VidTrans:

```text
VIDTRANS_N8N_WEBHOOK_URL=http://n8n:5678/webhook/vidtrans
VIDTRANS_N8N_WEBHOOK_SECRET=<shared-secret>
VIDTRANS_N8N_ALLOW_INSECURE_HTTP=1
```

`ALLOW_INSECURE_HTTP=1` chỉ chấp nhận trong Docker network dev. Production phải dùng HTTPS và giữ biến này bằng `0`.

## 4. Workflow tối thiểu

1. Webhook `POST /vidtrans`, Header Auth như trên, trả 2xx ngay.
2. Data Store/Postgres deduplicate theo `event_id` trước side effect.
3. Switch theo `event_type`.
4. Gửi notification/CRM hoặc gọi workflow con; không download video vào n8n.
5. Chỉ activate sau khi Header Auth và dedup đã được cấu hình.

VidTrans vẫn gửi thêm `X-VidTrans-Signature` HMAC-SHA256 để có thể xác minh ở reverse proxy/custom node. Header Auth là đường tích hợp thực dụng cho Webhook node chuẩn; HTTPS là lớp bảo vệ bắt buộc ngoài dev.
