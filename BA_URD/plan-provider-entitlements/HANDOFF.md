# Handoff

- Feature: `plan-provider-entitlements` — `IMPLEMENTED_LOCAL`, UAT chưa chạy.
- Quy tắc ở một chỗ: `backend/domain/plan_entitlements.py` (`PAID_ONLY_PROVIDERS`).
- Server enforce: tạo job/batch, retry job, tạo/render comic. UI chỉ hiển thị khóa.
- Lưu ý: danh sách provider tải một lần khi mở trang; sau khi admin kích hoạt gói, user cần tải lại trang để mở khóa.
