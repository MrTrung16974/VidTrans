# HANDOFF — VidTrans multi-tenant và thương mại hóa

## 1. Trạng thái

- Feature: `multi-tenant-commercial-platform`
- Workspace: VidTrans
- Cập nhật: `2026-10-11`
- Giai đoạn: người dùng yêu cầu bỏ qua URD; đang ở DEV, đã hoàn tất lát n8n workflow events và Admin operations page.
- Plan được duyệt: `PLAN.md` version `0.2`, ngày 2026-10-10.

## 2. Mục tiêu và phạm vi đã duyệt

- MVP nhiều người dùng, ưu tiên tài khoản cá nhân; giữ schema tenant/membership để mở rộng agency.
- Admin full quyền, user upload/dịch/download nguồn và đăng TikTok.
- Subscription giới hạn theo phút, mua thêm credit phút; thanh toán ưu tiên Việt Nam.
- File nguồn và kết quả lưu 60 ngày.
- n8n dùng cho automation metadata/tích hợp, không xử lý binary/media pipeline.
- Facebook/YouTube nằm trong roadmap; thời điểm MVP hay beta vẫn cần chốt.

## 3. File cần đọc

1. `BA_URD/multi-tenant-commercial-platform/ANALYSIS.md` version 0.2.
2. `BA_URD/multi-tenant-commercial-platform/PLAN.md` version 0.2.
3. `BA_URD/multi-tenant-commercial-platform/OPEN_QUESTIONS.md`.
4. `ARCHITECTURE.md` và các design hiện hữu được dẫn trong analysis khi viết URD.

## 4. Quyết định và việc còn mở

- Đã xác nhận: quota phút, credit mua thêm, Việt Nam, retention 60 ngày, Admin full quyền, MVP cá nhân.
- Deferred: ngân sách và hạ tầng pilot.
- Cần quyết định trong URD: công thức tính phút/retry, giá và phút từng gói, payment provider, Facebook/YouTube thuộc MVP hay beta, kiểm soát vận hành đối với quyền xem/tải của Admin.

## 5. Kiểm chứng

- Chỉ thay đổi tài liệu; đã chạy `git diff --check`, không có lỗi whitespace.
- Chưa chạy unit/integration test vì chưa thay đổi code.
- Không sửa các thay đổi sẵn có ở `backend/infrastructure/tiktok_browser.py` và `backend/tests/test_tiktok_browser.py`.

## 6. Thay đổi DEV đã thực hiện

- Durable workflow outbox và n8n dispatcher ở `backend/infrastructure/workflow_events.py`.
- Nối event upload/download/dịch/publish/terminal vào `backend/main.py`.
- Unit test ở `backend/tests/test_workflow_events.py`.
- Hướng dẫn cấu hình ở `docs/n8n-integration.md`.
- Chi tiết kiểm chứng ở `DEV/multi-tenant-commercial-platform/IMPLEMENTATION.md`.
- Admin API summary/list/replay và tab `/#admin` để quan sát/replay dead events.
- n8n dev stack ở `deploy/n8n/compose.dev.yml`; hướng dẫn tại `deploy/n8n/README.md`.

## 7. Bước tiếp theo

Khởi động n8n dev bằng secret do người vận hành cấp, tạo owner/workflow Header Auth và chạy SIT chống trùng với n8n thật. Sau đó tiếp tục identity/multi-tenant và Admin users/jobs/quota; billing vẫn chưa triển khai.

Prompt tiếp tục: `Dùng $feature-delivery-workflow. Đọc BA_URD/multi-tenant-commercial-platform/HANDOFF.md và các file được dẫn; tiếp tục DEV theo plan 0.2 đã duyệt, giữ nguyên thay đổi hiện có.`
