# Test report — 2026-10-23

- `tests/test_billing_reconciliation.py` + `tests/test_payments.py`: PASS.
- Full suite: 681 PASS, 4 FAIL có sẵn, không liên quan (phân trang comic/job đang sửa song song, timeout `test_http_frontend_still_serves_index`, đường dẫn `/private/var` trên macOS).
- JS parse `web/js/app.js`: PASS.
- UAT trình duyệt: NOT_RUN (Docker local đang tắt).
