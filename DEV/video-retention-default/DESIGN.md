# Design — Mặc định xoá video sau 30 ngày

## Quyết định

- Bật retention hệ thống theo mặc định.
- Thời hạn mặc định giữ nguyên 30 ngày qua `VIDTRANS_JOB_RETENTION_DAYS`.
- Cho phép opt-out rõ ràng bằng `VIDTRANS_RETENTION_ENABLED=0`.
- Không thay đổi các guard hiện có: chỉ xoá job terminal; không xoá video còn lịch đăng/publish chưa terminal; policy riêng theo tài khoản tiếp tục được ưu tiên.

## Mapping code và kiểm thử

- `backend/main.py`: fallback của `retention_enabled()` đổi từ tắt sang bật.
- `docker-compose.yml`: default runtime đổi sang `VIDTRANS_RETENTION_ENABLED=1` và 30 ngày.
- `.env.example`, `README.md`: công bố policy và cách tắt/đổi số ngày.
- Unit test xác nhận mặc định bật, explicit `0` tắt và số ngày mặc định là 30.

## Rủi ro và rollback

- Đây là thay đổi hành vi có xoá dữ liệu. Sweep chỉ chạy với job `completed|failed|cancelled` quá cutoff và dùng logic bảo vệ publish đang hoạt động hiện có.
- Rollback vận hành: đặt `VIDTRANS_RETENTION_ENABLED=0` rồi restart service.
