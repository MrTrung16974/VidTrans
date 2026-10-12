# CONFIG CONTRACT — Payment providers

Tất cả secret phải được cấp qua environment/secret manager và không được log.

| Biến | Bắt buộc | Mục đích |
|---|---:|---|
| `VIDTRANS_PAYMENT_ENABLED` | Không | Bật khả năng tạo đơn; mặc định tắt |
| `VIDTRANS_PUBLIC_BASE_URL` | Có với ví điện tử | HTTPS redirect/callback base URL |
| `VIDTRANS_BANK_BIN` | Có với bank | BIN ngân hàng nhận |
| `VIDTRANS_BANK_ACCOUNT_NO` | Có với bank | Số tài khoản nhận |
| `VIDTRANS_BANK_ACCOUNT_NAME` | Có với bank | Tên chủ tài khoản |
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

