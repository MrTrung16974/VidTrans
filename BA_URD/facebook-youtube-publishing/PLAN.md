# Kế hoạch — Đăng Facebook và YouTube

- Feature: `facebook-youtube-publishing`
- Phiên bản: `0.1`
- Trạng thái: `IN_REVIEW`
- Nguồn yêu cầu: yêu cầu người dùng ngày `2026-10-14`
- Approval record: để trống — chưa có xác nhận của người dùng

## 1. Phạm vi đề xuất

MVP bổ sung **Facebook Page Reels** và **YouTube video/Shorts upload** vào lõi social
publishing hiện tại, gồm OAuth/account discovery, direct publish, local schedule,
reconcile, UI và automated tests. Không deploy và không gọi tài khoản thật trong unit test.

## 2. Definition of Done

- BA: URD có UC/BR/AC/NFR, error/state mapping và traceability không còn câu hỏi chặn.
- DEV: hai adapter, auth routes, registry, UI và test được triển khai theo contract chung.
- TEST: testcase, targeted test và regression có kết quả thực thi rõ PASS/FAIL/BLOCKED.
- DEPLOY: contract biến cấu hình, manifest và runbook được chuẩn bị; SQL/Camunda ghi N/A nếu
  xác minh không có thay đổi tương ứng.

## 3. Kế hoạch thực hiện

| Bước | Đầu vào | Việc làm | File đầu ra | Kiểm chứng | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| 1. BA | `ANALYSIS.md`, quyết định Q_01/Q_02 | Viết UC kết nối/đăng/lịch/reconcile, rule metadata/quyền/lỗi | `URD.md`, `OPEN_QUESTIONS.md`, `REVIEW.md` | Tự review ID, luồng, AC và security | Người dùng duyệt phạm vi | PENDING |
| 2. Design | URD đã đủ quyết định chặn | Mapping adapter/routes/store/UI, OAuth/token, retry/checkpoint | `DEV/facebook-youtube-publishing/DESIGN.md` | Review compatibility, auth, idempotency | Tài liệu API/app config | PENDING |
| 3. Domain/backend | Design | Thêm enum/capability, Facebook và YouTube publisher, auth routes, đăng ký app | Source hiện hữu dưới `backend/` | Unit + contract + route tests | Meta/Google contract | PENDING |
| 4. Frontend | Capability/API | Thêm connection cards và publish panels; metadata/consent/status riêng | `backend/frontend/index.html`, JS/CSS liên quan | Syntax/static selector tests | Backend contract | PENDING |
| 5. Tests | Code hoàn tất | Bổ sung lỗi OAuth, refresh, multipart/resumable, timeout, reconcile, idempotency, authorization | `backend/tests/test_facebook_publisher.py`, `test_youtube_publisher.py`, test route/static | Targeted suite và regression | Mock HTTP, không gọi mạng thật | PENDING |
| 6. TEST docs | AC và kết quả test | Viết testcase/report/traceability | `TEST/facebook-youtube-publishing/*`, `TRACEABILITY.md` | Đối chiếu AC → test evidence | Test đã chạy | PENDING |
| 7. Deploy package | Code/test ổn định | Viết config contract, manifest, runbook/rollback; xác định N/A SQL/Camunda | `DEPLOY/facebook-youtube-publishing/*` | Review precheck/smoke/rollback | Credential test/staging | PENDING |
| 8. Handoff | Toàn bộ artifact | Ghi thay đổi, lệnh test, gap và bước staging | `HANDOFF.md`, `IMPLEMENTATION.md` | Đối chiếu Git diff + evidence | Các bước trên | PENDING |

## 4. Thiết kế sơ bộ cần được review

- Thêm `FACEBOOK`, `YOUTUBE` vào `SocialPlatform`; không đổi schema vì cột platform là text.
- `FacebookPublisher`: Page access token; initialize Reel → upload binary/CDN → finish;
  checkpoint `video_id`; status/reconcile qua Graph API phù hợp.
- `YouTubePublisher`: OAuth web-server với `youtube.upload`; resumable upload; metadata
  title/description/privacy/category; checkpoint video ID; refresh/reconcile processing.
- Auth/token store tách theo platform/account và không đưa secret vào attempt payload.
- Tạo JS workspace dùng contract chung nhưng giữ control đặc thù:
  - Facebook: Page, title/description, Reel, direct/local schedule.
  - YouTube: channel, title bắt buộc, description, privacy mặc định `private`, category,
    direct/local schedule.
- Giữ TikTok/Instagram hiện tại hoạt động; không refactor lớn UI trong cùng lát triển khai.

## 5. Rủi ro và cách xử lý

| Rủi ro | Mức | Xử lý |
|---|---|---|
| Meta endpoint/quyền thay đổi hoặc cần App Review | Cao | Version/config rõ, scope tối thiểu, staging smoke gate. |
| YouTube project chưa audit nên upload bị private | Cao | Default private, cảnh báo UI/runbook, không tuyên bố public thành công. |
| Timeout sau remote side effect gây trùng bài | Cao | Lưu checkpoint trước submit kế tiếp; reconcile thay vì retry mù. |
| Video lớn làm hỏng tiến trình upload | Trung bình | Chunk/resumable upload, progress và retry chỉ phần an toàn. |
| Account spoofing/khác tenant | Cao | Resolve account server-side từ security context/token store. |
| UI quá dài sau thêm nền tảng | Trung bình | Panel thu gọn theo platform; giữ một video/caption nguồn, metadata riêng. |

## 6. Tự review plan

- Nguồn và AS-IS: đủ để lập kế hoạch; Meta live documentation bị giới hạn truy cập nhưng
  official Postman collection và sample repository xác nhận flow Reels.
- Compatibility: thay đổi additive; schema hiện tại không cần migration theo quan sát ban đầu.
- Security: đã đưa OAuth CSRF, token handling, object ownership, least privilege vào gate.
- Testability: adapter HTTP được mock; không cần credential thật cho automated tests.
- Rollback: có thể bỏ adapter khỏi registry/ẩn UI mà không xóa attempt hoặc đổi schema.
- Còn hai quyết định nghiệp vụ Q_01/Q_02; đề xuất mặc định đã nêu để có thể tiếp tục sau duyệt.

