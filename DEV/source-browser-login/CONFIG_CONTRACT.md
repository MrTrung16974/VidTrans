# Hợp đồng cấu hình

| Biến | Mặc định | Mục đích |
|---|---|---|
| `VIDTRANS_YOUTUBE_BROWSER_CDP_URL` | `http://youtube-browser:9222` | CDP private endpoint |
| `VIDTRANS_YOUTUBE_BROWSER_PUBLIC_URL` | local noVNC port 5203 | URL iframe/noVNC phía người dùng |
| `VIDTRANS_FACEBOOK_BROWSER_CDP_URL` | `http://facebook-browser:9222` | CDP private endpoint |
| `VIDTRANS_FACEBOOK_BROWSER_PUBLIC_URL` | local noVNC port 5204 | URL iframe/noVNC phía người dùng |
| `VIDTRANS_YOUTUBE_PROXY` | trống | Proxy HTTP/SOCKS cho Chromium YouTube khi mạng/VPS chặn kết nối |
| `VIDTRANS_FACEBOOK_PROXY` | trống | Proxy HTTP/SOCKS cho Chromium Facebook khi mạng/VPS chặn kết nối |
| `VIDTRANS_CORPORATE_CA_FILE` | không có | File PEM/CRT CA của mạng kiểm tra HTTPS; dùng cùng `docker-compose.corporate-ca.yml` |

Production override public URL sang `/youtube-browser/...` và `/facebook-browser/...`. Không biến nào chứa mật khẩu/token. Profile nằm trong named Docker volume, cookie snapshot nằm dưới `backend/work` và không được trả qua API.
