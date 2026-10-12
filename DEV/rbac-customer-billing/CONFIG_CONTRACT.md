# CONFIG CONTRACT — Payment providers

Tất cả secret phải được cấp qua environment/secret manager và không được log.

| Biến | Bắt buộc | Mục đích |
|---|---:|---|
| `VIDTRANS_PAYMENT_ENABLED` | Không | Bật khả năng tạo đơn; mặc định tắt |
| `VIDTRANS_PUBLIC_BASE_URL` | Có với ví điện tử | HTTPS redirect/callback base URL |
| `VIDTRANS_BANK_BIN` | Có với bank | BIN ngân hàng nhận |
| `VIDTRANS_BANK_ACCOUNT_NO` | Có với bank | Số tài khoản nhận |
| `VIDTRANS_BANK_ACCOUNT_NAME` | Có với bank | Tên chủ tài khoản |
| `VIDTRANS_BANK_NAME` | Không | Tên hiển thị ngân hàng; mặc định `MB Bank` |
| `VIDTRANS_BANK_QR_TEMPLATE` | Không | Giao diện VietQR; mặc định `compact2` |
| `VIDTRANS_BANK_WEBHOOK_SECRET` | Có nếu tự động đối soát | HMAC-SHA256 secret xác minh webhook chuyển khoản |
| `VIDTRANS_VIETQR_CLIENT_ID` | Có với VietQR API | Client ID |
| `VIDTRANS_VIETQR_API_KEY` | Có với VietQR API | API key |
| `VIDTRANS_MOMO_PARTNER_CODE` | Có với MoMo | Merchant partner code |
| `VIDTRANS_MOMO_ACCESS_KEY` | Có với MoMo | Access key |
| `VIDTRANS_MOMO_SECRET_KEY` | Có với MoMo | HMAC secret |
| `VIDTRANS_MOMO_ENDPOINT` | Có với MoMo | Sandbox/production endpoint |
| `VIDTRANS_ZALOPAY_APP_ID` | Có với ZaloPay | Merchant app ID |
| `VIDTRANS_ZALOPAY_KEY1` | Có với ZaloPay | Ký create order |
| `VIDTRANS_ZALOPAY_KEY2` | Có với ZaloPay | Xác minh callback |
| `VIDTRANS_ZALOPAY_ENDPOINT` | Có với ZaloPay | Sandbox/production endpoint |

Mẫu tên biến không chứa credential thật nằm tại `deploy/payment.env.template`. Callback đối soát ngân hàng nhận JSON `order_id`, `amount`, `transaction_id` tại `POST /api/v1/billing/callbacks/bank`; chữ ký của raw body truyền bằng header `X-VidTrans-Signature: sha256=<hex>`.

