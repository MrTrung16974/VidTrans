# Job cancellation safety — test report

Ngày chạy: 2026-10-20
Môi trường: container tạm `docker run --rm --network none` từ image `vidtrans-vidtrans` (Python 3.11), mount bản copy `backend`; dữ liệu tổng hợp, mọi provider được mock. Python 3.14 máy local thiếu `cryptography` nên chỉ chạy được test không import `main` (store/scheduler: 30/30 PASS).

## Kết quả

- `python -m unittest tests.test_job_cancellation tests.test_job_store tests.test_job_scheduler tests.test_job_service tests.test_frontend_static tests.test_api_tenant_isolation`: **PASS** (lỗi duy nhất ở lần chạy đầu là thiếu `docker-compose.yml` do mount, đã mount lại).
- Full backend `python -m unittest discover -s tests -t .`: **443 test, 442 PASS, 1 ERROR** — `tests.test_douyin_qr_auth` import module `infrastructure.douyin_qr_auth` không tồn tại trong repo; lỗi có sẵn, không liên quan.
- `node --check` cho `app.js` (dạng module): PASS.

## Chưa thực hiện

- Không chạy video thật/E2E huỷ trên UI; không SIT/UAT; không deploy.
