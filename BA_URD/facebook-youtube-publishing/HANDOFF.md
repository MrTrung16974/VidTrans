# Handoff — Facebook và YouTube publishing

- Workspace: VidTrans
- Feature: `facebook-youtube-publishing`
- Cập nhật: `2026-10-14`
- Giai đoạn: code và automated test hoàn tất; chờ staging smoke/UAT.

## Mục tiêu và phạm vi

Bổ sung Facebook Page Reels và YouTube video/Shorts upload vào Publishing Hub, gồm
OAuth, direct/local schedule, trạng thái/reconcile, UI và tests. Không gồm Facebook
profile/Group, live stream, analytics hoặc deploy production.

## Đọc tiếp theo thứ tự

1. `BA_URD/facebook-youtube-publishing/ANALYSIS.md`
2. `BA_URD/facebook-youtube-publishing/PLAN.md`
3. Sau khi plan được duyệt: tạo/đọc `URD.md`, rồi `DEV/.../DESIGN.md`.

## Trạng thái và quyết định

- Plan 0.1 được người dùng cho phép bắt đầu triển khai ngày `2026-10-14`.
- Giả định đề xuất: Facebook Page Reels; YouTube upload chuẩn và để YouTube tự phân loại Shorts.
- Q_01: có cần Facebook video thường ngoài Reel không?
- Q_02: privacy YouTube mặc định; đề xuất `private`.

## Thay đổi hiện có cần giữ

- `backend/frontend/index.html` đang có thay đổi nhãn điều hướng thành
  “Đăng bài mạng xã hội” từ yêu cầu trước của người dùng.
- Source Facebook/YouTube, auth route, registry và UI đã được bổ sung.

## Kiểm chứng

- Đã kiểm kê contract, registry, route và UI hiện tại.
- Targeted suite cuối: 17 tests PASS; JS syntax và diff check PASS.
- Không đọc hoặc ghi file chứa secret/config bị cấm.

## Bước kế tiếp

Tiếp theo cấu hình credential test qua secret manager, chạy smoke Facebook Page Reel và
YouTube private upload, rồi cập nhật TEST_REPORT. Không đưa token/secret vào tài liệu/log.

