# Implementation — Mặc định xoá video sau 30 ngày

## Thay đổi

- `retention_enabled()` mặc định bật khi biến môi trường không được khai báo.
- Docker Compose đặt `VIDTRANS_RETENTION_ENABLED=1`, `VIDTRANS_JOB_RETENTION_DAYS=30`.
- README và `.env.example` mô tả cách đổi thời hạn hoặc opt-out.
- Giữ nguyên bảo vệ video có publish chưa terminal và override retention theo tài khoản.

## Kiểm chứng 2026-10-19

- `python -m unittest tests.test_retention_defaults tests.test_admin_accounts tests.test_tenant_governance`
- Kết quả: PASS 14/14.
- Chưa restart runtime: container PostgreSQL trong Compose không hiện diện tại thời điểm kiểm tra nên không thể preflight chính xác số bản ghi sẽ bị sweep; tránh kích hoạt xoá khi chưa xác minh database đích.
