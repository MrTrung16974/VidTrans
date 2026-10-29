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
