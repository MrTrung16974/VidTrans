# Phân tích tái kiến trúc upload TikTok trực tiếp và đặt lịch

## 1. Thông tin chung

- Feature: `tiktok-direct-scheduled-upload`
- Phiên bản: `0.1`
- Trạng thái: `DRAFT_FOR_REVIEW`
- Ngày phân tích: `2026-10-10`
- Nguồn yêu cầu: cho phép đăng trực tiếp và đặt lịch TikTok; tham khảo `MiniGlome/Tiktok-uploader` và `546200350/TikTokUploder`.
- Phạm vi tài liệu: phân tích và kế hoạch. Chưa thay đổi source code, database hoặc môi trường chạy.

## 2. Hiện trạng hệ thống

### CONFIRMED

- `backend/infrastructure/tiktok_publisher.py` đã tích hợp TikTok Content Posting API chính thức:
  - OAuth và refresh token;
  - Direct Post qua `/v2/post/publish/video/init/`;
  - Upload bản nháp qua `/v2/post/publish/inbox/video/init/`;
  - upload file theo chunk và lấy trạng thái qua `/v2/post/publish/status/fetch/`.
- Caption hiện được truyền vào trường `post_info.title`; API TikTok chính thức xử lý caption, hashtag và mention trong cùng trường này.
- `backend/infrastructure/job_store.py` và `backend/application/job_scheduler.py` đã có lịch bền vững trên SQLite. Job chỉ được claim khi `scheduled_for <= now`.
- `backend/main.py` đang dùng lịch nội bộ: video được render xong, giữ trên server, đến giờ worker mới gọi Direct Post.
- Hệ thống còn có luồng TikTok Studio/noVNC để người dùng đăng thủ công hoặc xử lý login/CAPTCHA.
- Trạng thái đăng hiện nằm trực tiếp trong payload của processing job, chưa có aggregate/record riêng cho mỗi lần publish.
- Khi lịch đăng thất bại, job xử lý video vẫn có thể bị đánh dấu `completed`; điều này làm trạng thái pipeline và trạng thái publish khó phân biệt.

### Hệ quả

- Upload trực tiếp đã tồn tại nhưng bị gắn chặt vào pipeline render, khó retry/reconcile độc lập.
- Lịch hiện tại phụ thuộc worker và file local còn tồn tại đúng thời điểm; server dừng lâu có thể đăng trễ.
- Chưa có idempotency record đủ mạnh để chứng minh một lần submit đã hay chưa tạo bài trên TikTok.
- Không thể chọn rõ `đăng ngay`, `lịch nội bộ`, `lịch native TikTok`, hoặc `upload bản nháp` bằng một contract thống nhất.
- Khó mở rộng nhiều tài khoản do token/profile hiện thiên về một account trên một deployment.

## 3. Kết quả nghiên cứu hai dự án tham khảo

### `MiniGlome/Tiktok-uploader`

- Xác thực bằng cookie `sessionid`, không dùng Content Posting API chính thức.
- Gọi các endpoint web private để xin credential upload, upload VOD theo chunk, commit file và gọi `/api/v1/item/create/`.
- Đưa `schedule_time` vào request tạo bài; README nêu yêu cầu business account và giới hạn lịch tối đa 10 ngày.
- Có xử lý hashtag suggestion và ký request upload theo kiểu AWS SigV4.

### `546200350/TikTokUploder`

- Cũng dùng `sessionid`, endpoint web private, domain theo region và tham số chữ ký `X-Bogus`.
- Tạo `creation_id`, upload asset rồi gọi `/tiktok/web/project/post/v1/` với caption, hashtag/mention và thời gian đặt lịch.
- Code tham khảo ràng buộc lịch tối thiểu khoảng 20 phút tính cả buffer upload, tối đa 10 ngày và căn mốc 5 phút.

### Bài học có thể tái sử dụng

- Chia upload thành các phase có checkpoint: init → chunk upload → finalize → create post → reconcile.
- Tách hashtag/mention thành metadata có cấu trúc khi provider yêu cầu.
- Validate lịch trước khi tốn băng thông upload.
- Hỗ trợ domain/region và capability theo account/provider.

### Không nên sao chép nguyên trạng

- Hai dự án phụ thuộc API web không công khai, cookie phiên đăng nhập và thuật toán chống bot có thể thay đổi bất kỳ lúc nào.
- Session có thể hết hạn, bị yêu cầu QR/OTP/CAPTCHA hoặc làm tài khoản bị giới hạn.
- Response “success” của endpoint web không đồng nghĩa bài đã public và không thay thế được reconciliation.
- Không đưa cookie/session, chữ ký, raw response hoặc caption nhạy cảm vào log.
- Nếu tái sử dụng code thay vì chỉ học ý tưởng, phải review license/attribution riêng; khuyến nghị triển khai clean-room sau contract nội bộ.

## 4. Đối chiếu API chính thức TikTok

- Direct Post chính thức hỗ trợ caption/hashtag/mention, privacy, duet/comment/stitch, cover timestamp và upload file theo chunk.
- API chính thức trả `publish_id` để poll hoặc nhận webhook trạng thái.
- Client chưa qua audit bị hạn chế về privacy; scope `video.publish` phải được TikTok phê duyệt và người dùng cấp quyền.
- Tài liệu Content Posting API hiện không công bố tham số đặt lịch cho video.
- Vì vậy cần phân biệt:
  - `LOCAL_SCHEDULE`: VidTrans chờ đến giờ rồi gọi Direct Post chính thức.
  - `TIKTOK_NATIVE_SCHEDULE`: gửi lịch lên endpoint web private; chỉ là capability thử nghiệm, không phải API production mặc định.

## 5. Mục tiêu đo được

1. Người dùng có thể chọn `Đăng ngay`, `Đặt lịch`, hoặc `Gửi bản nháp` sau khi xem video và caption.
2. Caption và hashtag đã duyệt được truyền nguyên vẹn đến provider; không fallback về tên file.
3. Mỗi yêu cầu publish có record và state machine riêng, không phụ thuộc trạng thái render job.
4. Retry/restart không tự tạo bài trùng trong các trường hợp đã có remote ID hoặc kết quả submit mơ hồ.
5. Lịch nội bộ sống qua restart, claim nguyên tử và ghi nhận đăng trễ.
6. Adapter lịch native TikTok, nếu được bật, bị cô lập bằng feature flag/account allowlist và có kill switch.
7. Có thể theo dõi phase, số byte, remote ID, lỗi phân loại và lịch sử attempt mà không lộ secret.
8. Có test contract cho provider, state transition, schedule, idempotency, crash recovery và fallback.

## 6. Kiến trúc TO-BE đề xuất

```text
UI / REST API
      |
TikTokPublishingService (use case + policy + idempotency)
      |
      +-- PublishRepository / Outbox (SQLite)
      +-- AccountCredentialStore
      +-- SchedulePolicy + CapabilityResolver
      +-- MediaStore
      |
TikTokPublishingGateway (port)
      +-- OfficialContentPostingAdapter       [primary]
      +-- WebSessionNativeScheduleAdapter     [experimental]
      +-- BrowserStudioAdapter                [manual fallback]
      |
Reconciler / Webhook handler / Scheduled worker
```

### Vai trò từng adapter

- `OfficialContentPostingAdapter`: tuyến production mặc định cho `DIRECT_NOW`, `LOCAL_SCHEDULE` và `INBOX_DRAFT`.
- `WebSessionNativeScheduleAdapter`: chỉ cung cấp `NATIVE_SCHEDULE` sau khi spike xác minh endpoint/capability; không tự động fallback âm thầm.
- `BrowserStudioAdapter`: mở TikTok Studio để người dùng hoàn tất bằng UI khi cần login/CAPTCHA hoặc provider tự động bị tắt.

### Nguyên tắc lựa chọn provider

1. Mặc định chọn API chính thức.
2. Với lịch, mặc định dùng lịch nội bộ rồi gọi API chính thức.
3. Chỉ dùng lịch native khi account được allowlist, feature flag bật, thời gian nằm trong capability và người dùng chọn rõ.
4. Nếu native schedule không khả dụng, trả lỗi có hành động hoặc yêu cầu người dùng xác nhận chuyển sang lịch nội bộ; không đổi provider ngầm.

## 7. Publish aggregate và state machine

### Record đề xuất: `tiktok_publish_attempts`

- `id`, `job_id`, `account_id`, `provider`.
- `request_mode`: `DIRECT_NOW | SCHEDULE | INBOX_DRAFT`.
- `schedule_mode`: `NONE | LOCAL | TIKTOK_NATIVE`.
- `requested_at`, `scheduled_for_utc`, `timezone`, `claimed_at`, `completed_at`.
- `caption_snapshot`, `caption_sha256`, `media_path`, `media_sha256`, `media_size`.
- `privacy_level`, `disable_comment`, `disable_duet`, `disable_stitch`, `cover_timestamp_ms`.
- `idempotency_key`, `status`, `phase`, `attempt_count`, `version`.
- `remote_publish_id`, `remote_upload_id`, `remote_post_id` nếu provider trả về.
- `last_error_code`, `last_error_class`, `last_error_message_safe`, `remote_state_ambiguous`.
- `created_at`, `updated_at`.

### State machine

```text
READY
  +--> SCHEDULED_LOCAL --> CLAIMED
  +--> UPLOADING

CLAIMED/UPLOADING --> UPLOADED --> SUBMITTING
SUBMITTING --> SUBMITTED --> PROCESSING_REMOTE --> PUBLISHED
SUBMITTING --> SCHEDULED_REMOTE
UPLOADING/SUBMITTING --> RETRYABLE_FAILED        (chỉ khi biết chưa tạo side effect)
UPLOADED/SUBMITTING --> NEEDS_RECONCILIATION     (kết quả mơ hồ)
ANY PRE-SUBMIT --> CANCELLED
ANY --> NEEDS_AUTH | FAILED
```

### Quy tắc retry/idempotency

- Client gửi hoặc server sinh `idempotency_key`; unique theo account + media snapshot + publish intent.
- Một processing job có thể có nhiều publish attempt lịch sử nhưng chỉ một attempt active cho cùng idempotency key.
- Lỗi trước `init upload`: retry có backoff.
- Đã có `remote_publish_id`: chỉ poll/reconcile, không khởi tạo bài mới.
- Timeout sau finalize hoặc create-post: chuyển `NEEDS_RECONCILIATION`; không tự submit lại.
- Retry thủ công phải hiển thị nguy cơ trùng nếu không xác minh được remote state.

## 8. Luồng nghiệp vụ

### Đăng ngay

1. Người dùng xem video, sửa caption/hashtag, chọn privacy và xác nhận.
2. API tạo publish attempt từ snapshot bất biến.
3. Worker validate media và creator capability.
4. Adapter init, stream chunk, lưu remote ID ngay khi nhận được.
5. Reconciler poll/webhook đến trạng thái cuối.
6. UI hiển thị `đã gửi`, `đang xử lý`, `đã public` thành các trạng thái khác nhau.

### Đặt lịch nội bộ

1. Chuẩn hóa giờ người dùng sang UTC, đồng thời lưu timezone hiển thị.
2. Tạo `SCHEDULED_LOCAL`; file được pin khỏi cleanup.
3. Worker claim nguyên tử khi đến hạn; đo `schedule_lag_seconds`.
4. Thực hiện luồng Direct Post chính thức.
5. Nếu worker bị dừng, attempt vẫn ở DB và được claim sau restart.

### Đặt lịch native TikTok thử nghiệm

1. Capability resolver kiểm tra account/session, region, business capability và cửa sổ lịch.
2. Validate mốc lịch theo rule đã xác minh bằng spike; ban đầu giả định 20 phút–10 ngày, bước 5 phút.
3. Upload và create project/post qua adapter private.
4. Persist toàn bộ remote identifiers an toàn và chuyển `SCHEDULED_REMOTE`.
5. Reconcile theo endpoint đã xác minh hoặc yêu cầu xác nhận thủ công nếu TikTok không cung cấp trạng thái đáng tin cậy.

### Gửi bản nháp

- Dùng API chính thức `video.upload`; trạng thái `INBOX_DELIVERED` phải nói rõ người dùng còn phải hoàn tất trong ứng dụng TikTok.

## 9. Security và vận hành

- OAuth token và web session được quản lý qua `AccountCredentialStore`; mã hóa at rest hoặc secret store, permission tối thiểu.
- Cookie chỉ được thu thập qua luồng kết nối tài khoản có kiểm soát; không nhận cookie trong request đăng bài thường.
- CSRF state, redirect URI allowlist, session expiry và account binding phải được kiểm tra.
- Không log token, cookie, upload URL có credential, request signature, raw response hoặc full caption.
- Egress chỉ đến hostname TikTok allowlist; upload URL phải HTTPS và đúng suffix/pattern mong đợi.
- Rate-limit theo account/provider và circuit breaker khi TikTok trả anti-spam/429.
- Feature flag cho adapter private: global kill switch, account allowlist, canary percentage bằng 0 mặc định.
- Metrics tối thiểu: success rate, phase latency, bytes uploaded, retry count, schedule lag, reconciliation age, auth expiry, duplicate-prevented count.

## 10. Phạm vi

### Trong phạm vi

- Tách publishing use case khỏi render pipeline.
- Port/adapter cho official, web-session experimental và browser fallback.
- Publish aggregate, persistence, state machine, idempotency và reconciliation.
- Đăng ngay, lịch nội bộ, upload draft; spike có kiểm soát cho lịch native.
- UI review → xác nhận → đăng/đặt lịch, status timeline, cancel lịch nội bộ.
- Unit, contract, integration, recovery, security và regression tests.
- Migration dữ liệu hiện có và rollout theo feature flag.

### Ngoài phạm vi bản đầu

- Tự động vượt CAPTCHA/OTP hoặc né anti-bot.
- Đảm bảo native schedule bằng endpoint private khi chưa có spike trên account test.
- Hủy/sửa bài đã lên lịch native nếu chưa xác minh endpoint chính thức/ổn định.
- Quản trị đội nhóm/phân quyền nhiều tenant đầy đủ.
- Tối ưu generation caption/hashtag; feature này chỉ bảo đảm truyền đúng snapshot đã duyệt.

## 11. Rủi ro chính

| Rủi ro | Mức độ | Giảm thiểu |
|---|---|---|
| API web private thay đổi hoặc vi phạm điều khoản nền tảng | Critical | Official-first, legal/product approval, flag mặc định off, kill switch |
| Timeout sau remote submit gây đăng trùng | Critical | checkpoint remote ID, ambiguity state, không auto retry hậu side-effect |
| Session hết hạn/CAPTCHA | High | health check, `NEEDS_AUTH`, browser fallback, không tự bypass |
| Server dừng đúng giờ lịch nội bộ | High | durable queue, late-publish policy, alert schedule lag, HA ở phase sau |
| File bị cleanup trước giờ đăng | High | media reference/pin + retention check |
| Caption truyền sai hoặc bị thay bằng filename | High | immutable caption snapshot, request contract tests, end-to-end assertion |
| Quota/audit của API chính thức | High | preflight creator info, clear error, quota metrics, app audit plan |
| Không xác minh được trạng thái native | High | phase gate; không GA native schedule trước khi có reconciliation |

## 12. Giả định và câu hỏi mở

- `ASSUMPTION A01`: API chính thức là provider production mặc định.
- `ASSUMPTION A02`: “đặt lịch” chấp nhận lịch nội bộ; native schedule là tùy chọn experimental.
- `ASSUMPTION A03`: Múi giờ mặc định là `Asia/Ho_Chi_Minh`, nhưng API lưu UTC và timezone gốc.
- `ASSUMPTION A04`: Giai đoạn đầu có một account active, schema vẫn chuẩn bị `account_id` để tránh khóa kiến trúc.
- `OPEN Q01`: Có chấp nhận rủi ro/điều khoản khi dùng API web private cho native schedule không?
- `OPEN Q02`: Khi máy chủ lên lại sau giờ đăng, đăng ngay, bỏ qua, hay yêu cầu duyệt nếu trễ quá ngưỡng?
- `OPEN Q03`: Native schedule có bắt buộc trong release đầu hay có thể release official direct + local schedule trước?
- `OPEN Q04`: Có cần nhiều tài khoản TikTok ngay release đầu không?

## 13. Tiêu chí nghiệm thu cấp cao

- Caption/hashtag snapshot trong UI trùng với payload adapter trong test contract.
- Double click, retry HTTP và restart worker không tạo hai active publish attempt.
- Direct publish có remote ID và trạng thái cuối được reconcile.
- Lịch nội bộ tồn tại qua restart, không bị cleanup media và chỉ claim một lần.
- Failure sau submit không tự upload lại.
- Native adapter tắt hoàn toàn bằng config mà không ảnh hưởng official flow.
- Secret không xuất hiện trong API response, structured log hoặc test snapshot.
- Rollback có thể đưa toàn bộ traffic về official/local schedule mà không mất attempt đang chờ.
