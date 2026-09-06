# Kết nối TikTok Việt Nam và đăng video từ VidTrans

Hướng dẫn đối chiếu mã nguồn và tài liệu TikTok ngày 06/09/2026.

## 1. Trạng thái và điều kiện

VidTrans đã có OAuth Login Kit, làm mới token, tải video bằng Content Posting API,
đăng ngay hoặc đặt lịch và tra cứu trạng thái bài đăng. Cấu hình hiện tại còn thiếu
Client Key, Client Secret và callback HTTPS thật; chưa hoàn thành kết nối tài khoản.

Trong tích hợp này, tài khoản TikTok Việt Nam đăng nhập qua TikTok tại `www.tiktok.com`
và gọi API `open.tiktokapis.com`. Không cần đổi endpoint sang tên miền Việt Nam.
Kết nối không phải là thiết lập nhắm mục tiêu người xem theo quốc gia.

Bạn cần có tài khoản TikTok muốn đăng video, ứng dụng TikTok Developer được cấp quyền
phù hợp và địa chỉ HTTPS truy cập được của VidTrans. Khả năng cấp quyền thực tế do
TikTok quyết định cho ứng dụng/tài khoản. Theo [hướng dẫn Direct Post](https://developers.tiktok.com/docs/en/content-posting-api-get-started),
phải bật Direct Post, được duyệt scope `video.publish` và được người dùng cấp scope đó.

## 2. Tạo ứng dụng TikTok Developer

1. Đăng nhập [TikTok for Developers](https://developers.tiktok.com/), vào Manage apps
   và tạo/chọn ứng dụng.
2. Điền thông tin ứng dụng, website và các URL chính sách mà cổng quản trị yêu cầu.
3. Thêm Login Kit cho Web và Content Posting API; bật Direct Post, yêu cầu `video.publish`.
4. Trong Login Kit, đăng ký chính xác callback của máy chủ, ví dụ:

   ```text
   https://video.example.com/api/v1/tiktok-auth/callback
   ```

5. Lấy Client Key và Client Secret của cùng ứng dụng/môi trường. Nếu dùng môi trường
   thử nghiệm, thêm tài khoản thử theo hướng dẫn trên cổng quản trị.

Thay `video.example.com` bằng domain thật. Callback phải là HTTPS, không có query
hoặc fragment; giữ đường dẫn và dấu `/` khớp với cấu hình.
Xem [Login Kit Web](https://developers.tiktok.com/docs/en/login-kit-web).

## 3. Cấu hình VidTrans

Mở `.env` tại thư mục gốc dự án, sửa các dòng đã có; không chép đè toàn bộ file vì
có thể làm mất cấu hình đăng nhập quản trị:

```dotenv
VIDTRANS_TIKTOK_CLIENT_KEY=YOUR_CLIENT_KEY
VIDTRANS_TIKTOK_CLIENT_SECRET=YOUR_CLIENT_SECRET
VIDTRANS_TIKTOK_REDIRECT_URI=https://video.example.com/api/v1/tiktok-auth/callback
VIDTRANS_TIKTOK_SCOPES=video.publish
```

Nhập secret trực tiếp trên máy chủ, không gửi qua chat hoặc commit vào Git.
Nếu chưa có `.env`, tạo từ `.env.example` trước khi sửa. Docker Compose đã truyền
các biến này vào backend. Trên VPS đã cài theo README, chạy từ thư mục gốc:

```bash
bash docker-rebuild.sh --production
```

Lệnh này cũng dùng cho cài đặt VPS lần đầu và sẽ hỏi thông tin domain/chứng chỉ,
tài khoản quản trị. Xem mục “Public portal trên VPS bằng Nginx” trong [README](../README.md).
Không cần mở cổng backend ra Internet. Nếu chạy Python trực tiếp, phải đưa các biến
trên vào môi trường tiến trình; việc có file `.env` riêng không đảm bảo Uvicorn tự đọc nó.

## 4. Kết nối tài khoản

1. Mở VidTrans qua domain HTTPS vừa cấu hình và đăng nhập quản trị.
2. Ở trang tạo video, tìm bước **Nội dung và đăng TikTok** → **Kết nối TikTok**.
3. Đăng nhập đúng tài khoản TikTok Việt Nam trên trang TikTok, kiểm tra tài khoản
   và chấp thuận quyền đăng video.
4. TikTok chuyển về VidTrans; kiểm tra thông báo đã kết nối.

Kết nối chỉ cấp quyền, chưa đăng video. API `GET /api/v1/tiktok-auth` trả trạng thái
`configured` và `connected`, không trả token; khi bật xác thực, dùng phiên quản trị.
`configured: true` chỉ cho biết đủ giá trị cấu hình, chưa chứng minh thông tin hợp lệ.
Mỗi cài đặt hiện lưu một tài khoản TikTok dùng chung; kết nối tài khoản khác sẽ thay
token hiện tại. Token được lưu trong thư mục work/tiktok-auth của backend.

## 5. Đăng thử và đặt lịch

1. Thêm một video bạn có quyền sử dụng, chọn cấu hình dịch/lồng tiếng.
2. Trong **Nội dung và đăng TikTok**, chọn **Tự động tạo: Có**.
3. Chọn **Đăng ngay sau khi gen video** và chủ động chọn **Chỉ mình tôi** khi thử.
4. Bấm **Bắt đầu xử lý**: thao tác này cho phép đăng tự động sau khi render xong.
5. Vào **Tiến trình**; sau khi gửi, bấm **TikTok** để cập nhật trạng thái và kiểm tra
   bài thực tế trong tài khoản TikTok. Có `publish_id` chưa có nghĩa bài đã đăng xong.

Muốn hẹn giờ, chọn **Đặt lịch đăng**, chọn ngày giờ và kiểm tra múi giờ hiển thị.
Nếu muốn giờ Việt Nam, trình duyệt/thiết bị cần dùng `Asia/Ho_Chi_Minh` (UTC+7).
Một lịch áp dụng cho cả batch; video đến hạn được xử lý lần lượt. Máy chủ cần chạy
và có mạng; thời điểm hiển thị bài còn phụ thuộc thời gian render và xử lý của TikTok.

Hiện pipeline gửi trường `title` trong `*.tiktok.json`, không gửi toàn bộ nội dung
trong file `*.tiktok.txt`. Để xem lại bản dịch, video và sửa caption trước khi đăng,
chọn **Không — chỉ tạo file**, tải video cùng nội dung TikTok rồi đăng thủ công trong
TikTok. Luồng tự động hiện chưa có bước duyệt video đầu ra và sửa caption sau render.

## 6. Giới hạn trước khi đăng công khai

Ứng dụng chưa audit chỉ đăng `SELF_ONLY`; tài khoản thử phải đặt riêng tư theo
[Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines).
Có Client Key không đồng nghĩa được đăng công khai. TikTok cũng không chấp nhận
Direct Post chỉ phục vụ công cụ nội bộ hoặc sao chép tùy ý nội dung nền tảng khác.

Mã nguồn hiện chưa đủ để khẳng định đạt audit: cần bổ sung hiển thị creator,
xem trước/sửa bài, lựa chọn quyền riêng tư không mặc định và theo API, điều khiển
tương tác, khai báo thương mại, xác nhận nhạc và kiểm tra thời lượng. Khi triển khai
video trên máy chủ cũng cần đối chiếu yêu cầu `PULL_FROM_URL` thay cho cơ chế
`FILE_UPLOAD` hiện tại. Đây là giới hạn triển khai, không thể khắc phục chỉ bằng
cách chọn “Công khai” trong giao diện.

## 7. Xử lý sự cố

| Hiện tượng | Cách kiểm tra |
| --- | --- |
| Chưa cấu hình Developer App | Điền đủ ba biến TikTok và tạo lại container để nhận môi trường mới. |
| Callback/redirect không hợp lệ | So sánh URL trong Developer Portal với `.env`, gồm HTTPS, domain, đường dẫn và dấu `/`. |
| Phiên kết nối hết hạn | Bấm Kết nối TikTok lại; state chỉ sống 10 phút và mất khi server restart. |
| Thiếu quyền đăng | Kiểm tra Direct Post và `video.publish`, sau đó kết nối lại để cấp quyền mới. |
| Không hỗ trợ mức hiển thị | Chọn mức tài khoản được API cho phép; app chưa audit dùng Chỉ mình tôi. |
| Render xong nhưng đăng lỗi | Xem lỗi TikTok riêng trên job; vẫn có thể tải video/subtitle đã tạo. |
| Đã gửi nhưng chưa thấy bài | Cập nhật trạng thái; TikTok xử lý bất đồng bộ, kiểm tra cả mục bài riêng tư. |
| Đăng nhập hết hiệu lực | Kết nối lại khi refresh token hết hạn hoặc quyền đã bị thu hồi. |

**Ngắt kết nối** xóa token trong VidTrans. Muốn thu hồi quyền phía TikTok, vào phần
quản lý ứng dụng được cấp quyền trong tài khoản TikTok. Trước khi đổi tài khoản,
hủy các job đang chờ đăng để tránh chúng dùng tài khoản mới.
