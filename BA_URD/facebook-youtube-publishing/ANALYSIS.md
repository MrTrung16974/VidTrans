# Phân tích tính năng — Đăng Facebook và YouTube

- Feature: `facebook-youtube-publishing`
- Phiên bản: `0.1-draft`
- Ngày: `2026-10-14`
- Trạng thái: `DRAFT`

## 1. Vấn đề và mục tiêu

VidTrans đã có Publishing Hub, lõi `social_publishing`, lịch đăng nội bộ và các adapter
TikTok/Instagram/Threads/LinkedIn/Pinterest. Giao diện hiện chỉ cho người dùng thao tác
đăng TikTok và Instagram. Người dùng yêu cầu bổ sung Facebook và YouTube.

Kết quả mong muốn:

- Một video đã render có thể được duyệt và đăng riêng lên Facebook Page và YouTube.
- Mỗi nền tảng có kết nối tài khoản, metadata, consent và trạng thái độc lập.
- Có thể đăng ngay hoặc đặt lịch bằng scheduler hiện tại mà không làm thay đổi hành vi
  TikTok/Instagram.
- Không tạo bài trùng khi request bị lặp, timeout hoặc tiến trình khởi động lại.

## 2. Bằng chứng AS-IS

| ID | Nguồn | Quan sát | Trạng thái |
|---|---|---|---|
| SRC_01 | `backend/domain/social_publishing.py` | Enum chưa có `FACEBOOK`, `YOUTUBE`; contract đã có direct/local schedule, checkpoint và reconcile. | CONFIRMED |
| SRC_02 | `backend/infrastructure/publishers/registry.py` | Adapter được đăng ký qua `PublisherRegistry`; capability được công bố cho UI. | CONFIRMED |
| SRC_03 | `backend/application/social_publish_routes.py` | REST API chung đã tạo/list/cancel/refresh publish attempt. | CONFIRMED |
| SRC_04 | `backend/frontend/index.html`, `backend/frontend/js/instagram.js` | Publishing Hub hiện có control thao tác cho TikTok và Instagram. | CONFIRMED |
| SRC_05 | `DEV/multi-platform-publishing/PLAN.md` | Feature cũ chủ động loại Facebook và YouTube khỏi phạm vi. | CONFIRMED |
| SRC_06 | Meta official Facebook API collection | Facebook Reels hỗ trợ quy trình initialize/upload/finish cho Page và trả `video_id`. | CONFIRMED |
| SRC_07 | Google YouTube Data API `videos.insert` | Upload video yêu cầu OAuth; hỗ trợ metadata và media upload. | CONFIRMED |
| SRC_08 | Google OAuth web-server guide | Scope tối thiểu phù hợp upload là `youtube.upload`; offline access dùng refresh token. | CONFIRMED |

## 3. AS-IS / TO-BE

### AS-IS

- Backend generic đủ dùng cho adapter mới nhưng platform enum/registry chưa có Facebook,
  YouTube.
- UI không có card kết nối hoặc panel đăng cho Facebook/YouTube.
- Store dùng attempt độc lập theo platform/account/idempotency key.
- Chưa có contract cấu hình, OAuth route, token store hoặc unit test cho hai nền tảng.

### TO-BE đề xuất

1. Người dùng kết nối một hoặc nhiều Facebook Page và YouTube channel bằng OAuth.
2. Publishing Hub cho chọn account/channel, nhập metadata đặc thù và chọn đăng ngay/đặt lịch.
3. Backend tạo attempt riêng cho từng nền tảng.
4. Facebook adapter upload Facebook Page Reel theo nhiều pha và lưu `video_id` làm checkpoint.
5. YouTube adapter resumable-upload video, lưu video ID, sau đó reconcile processing status.
6. Lỗi một nền tảng không ảnh hưởng video nguồn hoặc attempt của nền tảng khác.

## 4. Actor và phạm vi

| Actor | Trách nhiệm/quyền |
|---|---|
| Editor/Admin VidTrans | Chọn video, kết nối tài khoản, duyệt metadata, đăng hoặc đặt lịch. |
| Facebook Page | Nhận Reel qua Page access token và trả trạng thái xử lý. |
| YouTube channel | Nhận video qua OAuth của chủ channel và trả trạng thái upload/processing. |
| Scheduler VidTrans | Claim lịch đến hạn, chạy đúng một attempt và reconcile. |

### Trong phạm vi bản đầu

- Facebook **Page Reels**.
- YouTube upload video; hỗ trợ title, description, privacy và category cơ bản.
- Direct publish và local schedule.
- OAuth state một lần, account discovery, disconnect, refresh token khi nền tảng hỗ trợ.
- Progress, reconcile, idempotency, lỗi an toàn cho UI và unit/route/frontend tests.
- UI bổ sung connection card và publish panel cho Facebook/YouTube.

### Ngoài phạm vi

- Facebook profile cá nhân, Group, Story, live stream hoặc quảng cáo.
- YouTube live stream, playlist management, comment, thumbnail tùy chỉnh và captions upload.
- Analytics đa nền tảng.
- Deploy production, Meta App Review, Google OAuth verification/audit hoặc smoke test bằng
  tài khoản thật nếu chưa được cung cấp credential test.

## 5. Giả định và quyết định còn mở

| ID | Nội dung | Trạng thái | Ảnh hưởng |
|---|---|---|---|
| A_01 | “Đăng Facebook” nghĩa là đăng Reel lên Facebook Page do người dùng quản lý. | ASSUMPTION | Nếu cần profile/Group thì contract và khả năng API thay đổi đáng kể. |
| A_02 | “Đăng YouTube” dùng upload video chuẩn; YouTube tự phân loại Shorts theo nội dung/định dạng đủ điều kiện. | ASSUMPTION | Không có endpoint “upload Shorts” riêng trong phạm vi. |
| A_03 | Scheduler chung tiếp tục sở hữu lịch; không dùng native schedule ở bản đầu. | ASSUMPTION | Giữ state machine và rollback hiện tại. |
| Q_01 | Facebook có cần bài video thường ngoài Page Reel không? | OPEN | Quyết định option/UI và endpoint Meta. |
| Q_02 | YouTube mặc định `private`, `unlisted` hay `public`? | OPEN | Liên quan an toàn xuất bản và audit API project. Đề xuất `private`. |

## 6. Bảo mật và rủi ro

- OAuth state phải dùng một lần, có TTL và ràng buộc với user/tenant đã xác thực.
- Token không được trả về API công khai, ghi payload publish hoặc log.
- Scope tối thiểu; Facebook chỉ giữ Page access token cần thiết, YouTube ưu tiên
  `youtube.upload` và offline refresh.
- Submit không được retry mù sau khi đã có remote ID; chuyển reconcile khi kết quả mơ hồ.
- Kiểm tra ownership account/page/channel phía server, không tin `account_id` do client gửi.
- Google nêu rõ upload từ API project chưa được audit có thể bị giới hạn ở chế độ private;
  đây là rủi ro vận hành, không được hiển thị như đã đăng công khai.
- Meta/Google quota, app review và verification là phụ thuộc môi trường ngoài source code.

## 7. Tiêu chí nghiệm thu cấp cao

- `GET /api/v1/social-platforms` công bố Facebook và YouTube khi adapter được cấu hình.
- Người dùng kết nối/chọn đúng Facebook Page hoặc YouTube channel trước khi đăng.
- Direct/local schedule tạo attempt độc lập, chống double-click và phục hồi sau restart.
- Facebook/YouTube lưu checkpoint remote và reconcile mà không upload/submit trùng.
- UI hiển thị chính xác trạng thái đang upload/processing/published/failed; không coi
  response chấp nhận upload là xuất bản hoàn tất.
- Regression tests TikTok/Instagram và targeted tests mới đều pass.

