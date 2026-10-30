# Test report — Logo branding (2026-10-29)

## Tự động

- `python -m pytest tests/test_logo_branding.py` — 23/23 PASS: nhận diện (logo tĩnh, không logo,
  cảnh đứng yên → không chắc, logo giữa khung bị bỏ qua, logo đổi góc, nhiều logo, ứng viên yếu),
  kích thước 720p/1080p/4K/9:16 không méo, filter graph, store theo user, API cách ly user,
  bắt buộc logo + xác nhận quyền, render đầu-cuối giữ audio, xử lý lại, hủy không để file tạm.
- Toàn bộ suite: 855 passed, 1 skipped. `contracts/openapi.json` đã sinh lại (gồm cả độ lệch
  có sẵn từ các commit trước).

## Thủ công (video tổng hợp, FFmpeg local)

| Video | Kết quả |
|---|---|
| 1280×720, logo cố định góc trên-trái, có audio | Nhận diện đúng hộp (sai số < 1 %), che logo cũ + chèn đúng chỗ, giữ audio AAC |
| 720×1280 (9:16) không logo | `not_found` → chèn góc trên-phải |
| 1280×720, logo nhảy từ trên-trái sang dưới-phải ở giây 12 | 2 đoạn, logo mới theo đúng từng vị trí |

Nhận diện mất ~0.15 s cho video 20 s. Đã chạy giao diện `#account` thật qua server cách ly:
tải logo, xem trước 16:9/9:16, gửi video, lịch sử, tải video.

## Chưa kiểm

Video thật từ Douyin/TikTok (watermark bán trong suốt, chữ chạy) và triển khai Docker/VPS.

## Giai đoạn nâng cao (2026-10-29)

- `tests/test_logo_branding.py`: 34/34 PASS (thêm biểu thức quỹ đạo FFmpeg, rút gọn quỹ đạo,
  tách đoạn khi nhảy/lấp khe/bỏ khớp lẻ, dò mẫu logo di chuyển và vắng mặt, mặt nạ lấp lỗ,
  vá nền về màu xung quanh, render inpaint đầu-cuối xóa được viền đặc của logo cũ, API logo
  mẫu cách ly user + được chụp vào job).
- Toàn bộ suite: 866 passed, 1 skipped.

| Video tổng hợp | Kết quả |
|---|---|
| Watermark 60 % trượt liên tục + logo mẫu | Bám theo 22 điểm mốc, sai số ≤ 3.5 px; logo cũ được vá, logo mới chạy theo |
| Logo cố định, inpaint (không mẫu) | Logo cũ (kể cả viền đặc) bị xóa, nền xám khôi phục |
| Logo nhảy góc ở giây 12 + mẫu | 2 đoạn, không nội suy trượt giữa hai góc |
| 9:16 không logo + mẫu | `not_found`, góc mặc định |
| Khối đỏ đặc không chi tiết, không mẫu | `not_found` (giới hạn đã ghi trong DESIGN) |

Đã chạy lại trang `#account` qua server cách ly: tải logo mẫu, bật khôi phục nền, gửi video,
lịch sử hiển thị "theo mẫu · logo di chuyển · đã khôi phục nền".
