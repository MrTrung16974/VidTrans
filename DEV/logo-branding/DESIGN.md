# Design — Tự động nhận diện & chèn logo thương hiệu

## Phạm vi

Tích hợp vào VidTrans hiện có (không tạo hệ thống xử lý video thứ hai): tái sử dụng auth,
hàng đợi job PostgreSQL/SQLite, quota, hủy job, retention, lưu trữ theo tenant và FFmpeg.

| Kế hoạch gốc | Hiện thực trong VidTrans |
|---|---|
| `app/api/account.py`, `branding.py`, `video.py` | `backend/application/branding_routes.py` |
| `services/logo_detector.py`, `logo_tracker.py` | `backend/pipeline/logo_detection.py` (cạnh bền theo cửa sổ thời gian), `logo_tracking.py` (dò theo mẫu, logo di chuyển), `logo_inpaint.py` (khôi phục nền) |
| `services/logo_overlay.py` | `backend/pipeline/logo_overlay.py` |
| `services/video_processor.py`, `workers/video_worker.py` | `backend/application/branding_service.py` chạy trên `JobScheduler` sẵn có |
| `models/branding_config.py` | `backend/domain/branding.py` + bảng `user_branding_configs` |
| `models/video_job.py` | bảng `jobs` sẵn có (`job_action = "logo_branding"`, trường `branding`) |
| `models/user.py` | `admin_accounts` sẵn có |
| Celery + Redis | Không thêm: scheduler hiện tại đã có hàng đợi bền, retry, hủy, phục hồi sau restart |
| `templates/account.html` | View `#account` trong `web/index.html` + `web/js/branding.js` |

## API (`/api/v1`, đều yêu cầu đăng nhập, phạm vi theo `user_id`)

| Method | Endpoint | Chức năng |
|---|---|---|
| GET | `/api/v1/me` | Thông tin tài khoản (sẵn có) |
| PUT | `/api/v1/me` | Đổi tên hiển thị |
| GET/PUT | `/api/v1/me/branding` | Đọc / lưu cấu hình logo |
| GET/POST/DELETE | `/api/v1/me/branding/logo` | Xem / tải lên / xóa logo |
| GET/POST/DELETE | `/api/v1/me/branding/template` | Logo mẫu cần thay (tùy chọn) |
| POST | `/api/v1/branding/videos` | Gửi video (multipart: `file`, `rights_confirmed`, `override`) |
| GET | `/api/v1/branding/videos` | Lịch sử của chính người dùng |
| GET | `/api/v1/branding/videos/{job_id}` | Trạng thái, tiến độ, kết quả nhận diện |
| GET | `/api/v1/branding/videos/{job_id}/download` | Tải video thành phẩm |
| POST | `/api/v1/branding/videos/{job_id}/reprocess` | Chạy lại với vị trí xác nhận / ép góc |

Hủy job dùng `POST /api/v1/jobs/{job_id}/cancel` sẵn có. Trạng thái: `queued` (PENDING),
`processing`, `completed`, `failed`, `cancelled`.

## Thuật toán nhận diện (MVP, không cần huấn luyện)

1. Lấy mẫu ~1.5 frame/giây (tối đa 360 frame), xám, rộng 320 px.
2. Chia cửa sổ 6 giây. Mỗi cửa sổ: cạnh (gradient) xuất hiện ≥ 80 % số frame và điểm ảnh ít
   biến thiên → mặt nạ "đứng yên"; gom blob; loại blob ở giữa khung, quá to/nhỏ, viền letterbox.
3. Điểm tin cậy = độ bền cạnh + chênh lệch chuyển động giữa vùng quanh và trong blob + vị trí
   (góc > cạnh) + mật độ.
4. Cửa sổ mà cả cảnh đứng yên (slide, camera cố định) là "không chắc" và kế thừa cửa sổ bên cạnh,
   tránh tự ý ghi đè nhầm vùng.
5. Gom ứng viên giữa các cửa sổ thành track (IoU > 0.3); mỗi cửa sổ chọn 1 logo theo chiến lược
   (gần vị trí mặc định / lớn nhất / chắc nhất) → các đoạn thời gian có/không có logo.

| Trường hợp | Xử lý |
|---|---|
| 1 logo | Chèn logo mới căn giữa vùng logo cũ (trong lề) |
| Không logo | Góc mặc định (trên-phải) |
| Nhiều logo | Theo `multi_logo_strategy` |
| Logo đổi vị trí | Mỗi đoạn một vị trí (`overlay ... enable=`) |
| Logo xuất hiện một phần | Đoạn không có logo dùng vị trí mặc định |
| Không chắc chắn | Vị trí mặc định; tùy chọn đánh dấu `needs_review` để người dùng chọn lại |

Logo cũ chỉ được xóa khi bật `cover_original`; người dùng phải xác nhận có quyền chỉnh sửa
video khi gửi.

## Giai đoạn nâng cao (2026-10-29)

### Logo mẫu & theo dõi logo di chuyển — `pipeline/logo_tracking.py`

Người dùng có thể tải "logo mẫu cần thay" (`/api/v1/me/branding/template`, bảng
`user_branding_templates`, được chụp vào job khi gửi). Khi có mẫu, job dò đúng logo đó và
**không** thay logo khác nếu không tìm thấy:

1. Gradient (Sobel) làm mịn σ=1.5 của mẫu và frame — chịu được watermark bán trong suốt,
   nền thay đổi và lệch 1–2 px.
2. Tìm tỉ lệ: 12 frame rải đều, 20 tỉ lệ (3–50 % chiều rộng), xếp theo độ nổi bật của đỉnh
   (z-score); mẫu < 28×14 px bị bỏ (mẫu tí hon khớp mọi nơi).
3. Dò 4 frame/giây (`fps=...:round=up` để mốc thời gian không trễ nửa khoảng). Khớp khi
   điểm ≥ 0.75 và z ≥ 7, hoặc điểm ≥ 0.83 và z ≥ 5.
4. Gom thành đoạn: tách khi mất > 2 mẫu hoặc nhảy > 12 % khung (logo đổi góc), bỏ đoạn < 3
   mẫu, lấp khe nhỏ hơn độ phân giải lấy mẫu. Lọc trung vị 3 mẫu, rút gọn quỹ đạo bằng
   Douglas–Peucker (dung sai 0.4 % khung, tối đa 120 điểm mốc).
5. FFmpeg di chuyển logo mới bằng `overlay=x='…':y='…'`: tổng phẳng các đoạn tuyến tính có
   cổng thời gian (không lồng `if`).

Đo trên video tổng hợp 1280×720 có watermark 60 % trượt liên tục: sai số vị trí ≤ 3.5 px,
16 s video dò trong ~2.3 s (CPU).

### Khôi phục nền — `pipeline/logo_inpaint.py`

`cover_method = "inpaint"` (mặc định) thay cho `delogo`:
FFmpeg giải mã CFR BGR → Python vá đúng các điểm ảnh của logo bằng `cv2.inpaint` (Telea) →
FFmpeg mã hóa + chèn logo mới, audio lấy từ file gốc. Mặt nạ:
- nhận diện tự động: cạnh bền + các điểm ảnh *đứng yên* liền kề (thân/viền đặc của logo không
  có cạnh riêng khi nền đổi màu), đóng khe và lấp lỗ;
- logo mẫu: kênh alpha của mẫu (mẫu không trong suốt → cả hình chữ nhật);
- nới thêm 1 % cạnh ngắn để phủ sai số dò; đi theo quỹ đạo nếu logo di chuyển.

`cover_method = "blur"` giữ `delogo` (nhanh, chỉ logo đứng yên). Nếu thiếu OpenCV, job tự
chuyển về blur. OpenCV có sẵn trong image Docker (PaddleOCR → `opencv-contrib-python`).
Kích thước khung lấy từ frame giải mã thật, nên video điện thoại có metadata xoay vẫn đúng.

### Giới hạn còn lại

- Inpainting cổ điển: logo đặc, lớn trên nền nhiều chi tiết để lại vệt mờ; nên chọn logo mới
  đủ lớn để che. Mô hình học sâu (LaMa/ProPainter, cần GPU) là bước tiếp theo nếu cần.
- Logo khối màu đặc không có chi tiết bên trong và không có mẫu: không nhận diện được tự động
  (cạnh duy nhất là với nền, mà nền thay đổi) → dùng logo mẫu.
- Mỗi tài khoản một logo mẫu.

## Render

Logo rộng `logo_size` % chiều rộng video (giữ tỉ lệ, không vượt 30 % chiều cao), lề `margin` %
cạnh ngắn, `opacity` qua `colorchannelmixer`. H.264 CRF 20, giữ nguyên audio (copy), tự chuyển
sang AAC nếu container không nhận codec gốc. File tạm `.part.mp4` bị xóa khi lỗi/hủy.
Logo và cấu hình được chụp vào job khi gửi, đổi cấu hình sau đó không ảnh hưởng job đang chạy.

## Giới hạn

Logo ≤ 5 MB, 16–4096 px (PNG/WebP/JPEG, lưu lại thành PNG, tự cắt viền trong suốt);
video ≤ 2048 MB (bằng giới hạn nginx) và phải qua `validate_video_file`.
