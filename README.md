# VidTrans — Video Translator Web App

## Cấu trúc
```
video_translator/
├── backend/
│   └── main.py          ← FastAPI server
├── frontend/
│   └── index.html       ← Giao diện web
├── requirements.txt
└── README.md
```

## Cài đặt

```bash
pip install -r requirements.txt -r requirements-downloader.txt
```

> Cần có **ffmpeg** trong PATH:
> - Windows: https://ffmpeg.org/download.html
> - Mac: `brew install ffmpeg`
> - Linux: `sudo apt install ffmpeg`

## Chạy server

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Mở trình duyệt: **http://localhost:8000**

## Public portal trên VPS bằng Nginx

Trên VPS Linux (Contabo hoặc nhà cung cấp khác), nên trỏ bản ghi DNS `A` của domain về IPv4 của
VPS trước khi chạy. Nếu có bản ghi `AAAA`, bản ghi đó cũng phải trỏ đúng IPv6 của VPS. Nếu chưa
có domain, trình cài đặt cũng nhận trực tiếp IPv4 public và dùng chứng chỉ IP ngắn hạn. Trong
firewall của nhà cung cấp/VPS, chỉ cần mở TCP `22`, `80` và `443`; không mở cổng `5200` ra
Internet vì FastAPI đã được bind vào `127.0.0.1` và lưu lượng public phải đi qua Nginx.

Sau khi cài Docker Engine cùng Docker Compose plugin, chạy đúng một lần:

```bash
bash docker-rebuild.sh --production
```

Trình cài đặt sẽ hỏi domain/IPv4, email Let's Encrypt, tài khoản và mật khẩu quản trị. Nó tự động:

1. Sinh password hash và JWT secret, không ghi mật khẩu thô xuống đĩa.
2. Tạo `.env` và `.env.production` với quyền chỉ owner đọc được, đồng thời giữ nguyên cấu hình
   TikTok đã có trong `.env`.
3. Khởi động Nginx HTTP chỉ để hoàn thành ACME challenge.
4. Xin chứng chỉ Let's Encrypt, chuyển sang HTTPS và bật portal tại domain đã nhập.
5. Khởi động Certbot renew mỗi 12 giờ; Nginx reload chứng chỉ định kỳ.

Sau lần đầu, marker `deploy/.vps-configured` khiến lệnh bình thường luôn giữ Nginx/Certbot chạy:

```bash
# Chỉ build lại source code, không tải lại apt/pip/model
bash docker-rebuild.sh

# Chỉ dùng khi dependencies thay đổi
bash docker-rebuild.sh --full
```

Để đổi domain hoặc sinh lại tài khoản/secret, chạy `bash deploy/vps-first-run.sh --force` trên VPS.
Không commit hoặc sao chép `.env`, `.env.production`, `deploy/.vps-configured` lên Git. Nginx giới
hạn upload 2 GB, tắt request buffering cho video lớn, redirect HTTP sang HTTPS và thêm các security
header cho portal. Cấu hình production nằm trong `docker-compose.production.yml`; cấu hình local
vẫn chạy tại `http://localhost:5200` và có thể ép bằng `bash docker-rebuild.sh --local`.

## Sử dụng

1. Kéo một hoặc nhiều **video** vào vùng upload, hoặc dán URL/nguyên đoạn chia sẻ TikTok và
   Douyin. Có thể trộn cả file lẫn link, tối đa tổng cộng 50 video mỗi batch. Link rút gọn như
   `https://v.douyin.com/...`, `https://vm.tiktok.com/...` được tự nhận diện.
2. Đặt tên batch và chọn cấu hình dùng chung
3. Upload **nhạc nền** nếu dùng mode 3 (mp3, wav, aac...)
3. Chọn nguồn phụ đề Trung:
   - `Whisper`: nhận diện hoàn toàn từ giọng nói, là lựa chọn mặc định trên Docker Apple Silicon
   - `Tự động`: ưu tiên OCR dòng chữ đã có trên video, dùng Whisper làm dự phòng
   - `OCR`: bắt buộc đọc phụ đề Trung đóng cứng trên hình
4. Nếu dùng OCR, chỉnh vùng đọc theo tỉ lệ chiều cao video khi phụ đề không nằm gần đáy
5. Chọn chế độ giọng đọc:
   - `Tự động theo cao độ giọng gốc`: chạy hoàn toàn local, dùng pitch của từng câu để chọn chất giọng TTS Nam/Nữ.
   - `Chọn thủ công`: dùng một chất giọng Nam hoặc Nữ cho toàn bộ video.
   - Trong chế độ tự động, `Giọng dự phòng` được dùng khi lời thoại quá ngắn, nhiễu hoặc pitch không đủ chắc chắn.
6. Chọn cách đặt phụ đề: thay đúng vị trí chữ Trung, đặt phía trên chữ Trung hoặc vùng an toàn
7. Chọn có tự động tạo nội dung TikTok hay không; có thể giới hạn độ dài tóm tắt và số hashtag.
   Nếu đã kết nối TikTok, có thể bật tự động đăng video đầu ra bằng đúng tiêu đề vừa tạo.
8. Bấm **Bắt đầu xử lý**, sau đó chuyển sang trang **Tiến trình**
9. Hủy, retry, xóa hoặc tải toàn bộ kết quả ZIP cho từng video

Lần OCR đầu tiên sẽ tải model nhận diện chữ Trung và có thể mất nhiều thời gian hơn các lần sau.
Docker Compose lưu model này trong volume `paddle-models`.
Docker Apple Silicon tắt PaddleOCR mặc định (`VIDTRANS_ENABLE_PADDLE_OCR=0`) vì Paddle có thể làm
process Linux ARM64 bị crash. Chỉ bật lại sau khi đã kiểm tra runtime Paddle tương thích.
Mỗi job còn xuất file `*.translation.json` chứa chữ Trung, bản dịch Việt, confidence OCR,
kết quả đối chiếu ASR, cờ `needs_review` và thông tin chọn giọng cho từng câu.

Phần dịch gom nhiều cue vào một request có marker ổn định để tránh giới hạn do gọi Google theo
từng câu. Nếu marker bị thay đổi, hệ thống tự dịch lại riêng các cue của batch đó; câu vẫn không
dịch được được gắn `translation_status=source_fallback`, `needs_review=true` và đếm trên job thay
vì âm thầm coi là thành công. Renderer luôn dùng font Noto Sans CJK SC được nhúng trong image nên
hiển thị đầy đủ dấu tiếng Việt và cả tên riêng/chữ Trung dự phòng, không còn ký tự ô vuông.

Ba chế độ xử lý chỉ bật đúng cấu hình liên quan. Vietsub giữ nguyên audio nguồn và bỏ qua TTS;
lồng tiếng tự co các câu đọc dài về đúng cửa sổ subtitle; chế độ có nhạc lặp/fade nhạc theo thời
lượng video rồi cân âm chung. Video không có audio nguồn vẫn chạy được ở hai chế độ lồng tiếng.

Mặc định, sau bước dịch hệ thống tự tạo nội dung đăng TikTok hoàn toàn local, không cần API key
riêng. File `*.tiktok.txt` được trình bày sẵn để sao chép, gồm tiêu đề, hook, tóm tắt,
caption và hashtag. File `*.tiktok.json` chứa cùng dữ liệu ở dạng có cấu trúc để sau này nối
với lịch đăng bài hoặc một nhà cung cấp AI khác. Các câu trùng được loại bỏ và câu có cờ
`needs_review` bị hạ ưu tiên để hạn chế đưa bản dịch chưa chắc chắn vào hook.

### Xác thực OAuth2 + JWT

Xác thực quản trị mặc định **tắt** để không khóa phiên bản cài đặt cũ. Để bật, tạo `.env` từ
`.env.example`, sau đó sinh mật khẩu băm và JWT secret ngay trên máy (không gửi hai giá trị này
qua chat hoặc commit vào Git):

```bash
docker compose run --rm vidtrans python -m infrastructure.auth
```

Sao chép hai dòng được tạo vào `.env`, đặt `VIDTRANS_AUTH_ENABLED=1` và
`VIDTRANS_AUTH_USERNAME=admin`, rồi chạy lại `bash docker-rebuild.sh`. Giao diện sẽ hiện màn hình
đăng nhập; API nhận `Authorization: Bearer <jwt>`. Trình duyệt dùng cùng JWT trong cookie
`HttpOnly`, `SameSite=Strict`; mọi request thay đổi dữ liệu từ cookie còn phải có header chống
CSRF. Khi chạy sau reverse proxy HTTPS, bắt buộc đặt `VIDTRANS_AUTH_COOKIE_SECURE=1`.

JWT dùng thuật toán cố định HS256, có `iss`, `aud`, `sub`, `scope`, `iat`, `nbf`, `exp` và `jti`.
Mật khẩu chỉ lưu dạng PBKDF2-HMAC-SHA256; hệ thống giới hạn đăng nhập sai liên tiếp theo client.
Đăng xuất xóa cookie hiện tại, nhưng JWT Bearer đã cấp không có cơ chế thu hồi riêng; để vô hiệu
hóa toàn bộ token ngay lập tức, đổi `VIDTRANS_JWT_SECRET` rồi restart dịch vụ.

### Tự động đăng TikTok

VidTrans dùng Login Kit và Content Posting API chính thức. Tạo TikTok Developer App, bật quyền
`video.publish`, đăng ký đúng HTTPS redirect URI, sau đó sao chép
`.env.example` thành `.env` và điền ba giá trị TikTok. Không đưa file `.env` hoặc client secret
vào Git. Sau khi `docker compose up`, bấm **Kết nối TikTok** trên giao diện và cấp quyền một lần.

Tự động đăng mặc định tắt để tránh đăng ngoài ý muốn. Khi bật, pipeline chỉ đăng sau khi video
đã render thành công và dùng chính trường `title` trong file `*.tiktok.json`. TikTok xử lý bài
đăng bất đồng bộ; dashboard lưu `publish_id`, hiển thị trạng thái và cho phép bấm **TikTok** để
cập nhật. Lỗi đăng được ghi riêng, video/subtitle đã tạo vẫn được giữ để tải xuống. Ứng dụng
TikTok chưa qua kiểm duyệt có thể chỉ cho đăng ở chế độ riêng tư.

Trạng thái batch và job được lưu bền vững tại `backend/work/jobs.sqlite3`; restart server không
làm mất hàng đợi. Scheduler mặc định chạy tối đa hai pipeline, nhưng Whisper và OCR chỉ có một
slot để tránh tràn RAM/GPU. File nguồn được giữ để hỗ trợ retry và chỉ bị xóa khi người dùng xóa
job. Xem [ARCHITECTURE.md](ARCHITECTURE.md) để biết ranh giới module.

Video từ TikTok/Douyin được tải bằng `yt-dlp` bên trong worker, vì vậy API tạo batch trả kết quả
ngay và dashboard hiển thị riêng tiến độ tải nguồn. Hệ thống chỉ chấp nhận domain TikTok/Douyin,
không tải playlist, giới hạn mặc định 2 GB và 120 phút cho mỗi video, đồng thời xóa file `.part`
khi tải lỗi hoặc bị hủy. Douyin thường yêu cầu cookie mới kể cả với video công khai: xuất
`cookies.txt` dạng Netscape từ trình duyệt đang mở được Douyin rồi chọn file trong ô tùy chọn.
Mỗi job nhận một bản cookie riêng với quyền file hạn chế; bản này tự xóa ngay khi tải thành công
và không xuất hiện trong API hoặc gói kết quả. Có thể cấu hình một cookie dùng chung bằng cách
mount file vào container rồi đặt `VIDTRANS_YTDLP_COOKIE_FILE` trỏ tới đường dẫn đó.

`requirements-downloader.txt` được đặt ở Docker layer riêng. Khi thêm/cập nhật `yt-dlp`, Docker
vẫn tái sử dụng layer Torch/Paddle lớn nếu `requirements.txt` không đổi.

## Cấu hình chạy nền

```env
VIDTRANS_WORKER_CONCURRENCY=2
VIDTRANS_WHISPER_CONCURRENCY=1
VIDTRANS_OCR_CONCURRENCY=1
VIDTRANS_RECOVER_INTERRUPTED_JOBS=1
# TikTok Developer App (chỉ cần khi dùng tự động đăng):
VIDTRANS_TIKTOK_CLIENT_KEY=...
VIDTRANS_TIKTOK_CLIENT_SECRET=...
VIDTRANS_TIKTOK_REDIRECT_URI=https://your-domain.example/api/v1/tiktok-auth/callback
VIDTRANS_TIKTOK_SCOPES=video.publish
# OAuth2/JWT quản trị (sinh password hash và secret bằng lệnh ở trên):
VIDTRANS_AUTH_ENABLED=1
VIDTRANS_AUTH_USERNAME=admin
VIDTRANS_AUTH_PASSWORD_HASH=pbkdf2_sha256$...
VIDTRANS_JWT_SECRET=...
VIDTRANS_JWT_ISSUER=vidtrans
VIDTRANS_JWT_AUDIENCE=vidtrans-api
VIDTRANS_JWT_TTL_MINUTES=480
VIDTRANS_AUTH_COOKIE_SECURE=1
# Tùy chọn, chỉ cần cho video yêu cầu đăng nhập/giới hạn vùng:
VIDTRANS_YTDLP_COOKIE_FILE=/app/secrets/yt-dlp-cookies.txt
```

Tăng `VIDTRANS_WORKER_CONCURRENCY` chỉ khi máy đủ CPU/RAM. Trên GPU hoặc máy có RAM hạn chế,
nên giữ Whisper/OCR ở một slot.

## API quản lý

- `GET /api/v1/auth/status` — trạng thái bật/cấu hình/đăng nhập, không trả secret
- `POST /api/v1/auth/token` — OAuth2 password token endpoint; trả Bearer JWT và đặt cookie HttpOnly
- `DELETE /api/v1/auth/session` — đăng xuất phiên web hiện tại
- `POST /api/v1/batches` — tạo batch từ nhiều file và/hoặc trường `source_links` chứa link TikTok/Douyin
- `GET /api/v1/batches` — danh sách batch
- `GET /api/v1/batches/{batch_id}` — chi tiết batch và các job
- `GET /api/v1/jobs` — danh sách, tìm kiếm, lọc và phân trang job
- `GET /api/v1/jobs/{job_id}` — chi tiết một job
- `POST /api/v1/jobs/{job_id}/cancel` — yêu cầu dừng an toàn
- `POST /api/v1/jobs/{job_id}/retry` — tạo job mới từ cấu hình cũ
- `POST /api/v1/jobs/{job_id}/refresh-tiktok-status` — cập nhật trạng thái bài đăng TikTok
- `DELETE /api/v1/jobs/{job_id}` — xóa job và artifact
- `GET /api/v1/jobs/{job_id}/download-all` — tải toàn bộ kết quả dạng ZIP
- `GET /api/v1/tiktok-auth` — trạng thái kết nối TikTok (không trả token)
- `GET /api/v1/tiktok-auth/connect` — tạo URL OAuth kết nối TikTok
- `GET /api/v1/tiktok-auth/callback` — callback OAuth đã đăng ký với TikTok
- `DELETE /api/v1/tiktok-auth` — xóa token và ngắt kết nối

`POST /process-video`, `GET /status/{job_id}` và `GET /download/{filename}` vẫn được giữ để
tương thích với client cũ.

## Subtitle bám chữ gốc

Khi OCR phát hiện phụ đề đóng cứng, hệ thống giữ bbox qua toàn bộ pipeline, lấy trung vị vị trí
qua nhiều frame để chống rung và quy đổi từ vùng crop về độ phân giải video. Renderer sử dụng
tọa độ và cỡ chữ riêng cho từng cue. Nếu không có bbox (ví dụ dùng Whisper), subtitle tự chuyển
sang vùng an toàn gần đáy video.

## Thư mục tự tạo

- `uploads/` — video nguồn được giữ để retry cho đến khi xóa job
- `outputs/` — video đã dịch, subtitle, bản đối chiếu và nội dung TikTok
