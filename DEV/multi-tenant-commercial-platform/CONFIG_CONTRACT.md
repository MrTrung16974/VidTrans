# CONFIG CONTRACT — n8n workflow integration

| Biến | Kiểu | Bắt buộc | Default an toàn | Mục đích |
|---|---|---|---|---|
| `VIDTRANS_N8N_WEBHOOK_URL` | Absolute URL | Không | rỗng, integration tắt | Webhook production của n8n; ngoài localhost bắt buộc HTTPS |
| `VIDTRANS_N8N_WEBHOOK_SECRET` | Secret string | Có khi URL được bật | không có | Ký HMAC-SHA256 raw request body |
| `VIDTRANS_N8N_TIMEOUT_SECONDS` | Integer 1–30 | Không | `5` | Timeout mỗi lần POST |
| `VIDTRANS_N8N_MAX_ATTEMPTS` | Integer 1–20 | Không | `8` | Số lần giao tối đa trước khi chuyển event sang `dead` |
| `VIDTRANS_N8N_ALLOW_INSECURE_HTTP` | Boolean | Không | `0` | Chỉ bật cho mạng Docker dev tin cậy; cho phép webhook HTTP ngoài localhost |

Secret phải được cấp qua secret manager hoặc file môi trường ngoài Git. Không log, không trả qua API và không đặt trong workflow export.
