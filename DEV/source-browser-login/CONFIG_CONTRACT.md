# Hợp đồng cấu hình

| Biến | Mặc định | Mục đích |
|---|---|---|
| `VIDTRANS_*_BROWSER_CDP_URL` | `http://social-browser:9222` | Cả ba manager dùng chung CDP private endpoint |
| `VIDTRANS_SOURCE_BROWSER_PUBLIC_URL` | local noVNC port 5201 | URL iframe/noVNC chung |
| `VIDTRANS_SOCIAL_BROWSER_PROXY` | trống | Proxy HTTP/SOCKS chung cho Chromium nguồn |
| `VIDTRANS_CORPORATE_CA_FILE` | không có | File PEM/CRT CA của mạng kiểm tra HTTPS; dùng cùng `docker-compose.corporate-ca.yml` |

Production override public URL sang `/source-browser/...`. Profile nằm trong named Docker volume, cookie snapshot nằm dưới `backend/work` và không được trả qua API.
