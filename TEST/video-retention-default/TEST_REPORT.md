# Test report — Mặc định retention 30 ngày

- Ngày chạy: 2026-10-19.
- Unit/regression: 14 PASS, 0 FAIL.
- Runtime restart/sweep: NOT_RUN vì PostgreSQL service không xuất hiện trong `docker compose ps`; không kích hoạt thao tác xoá trước khi xác minh database đích và số job đủ điều kiện.
