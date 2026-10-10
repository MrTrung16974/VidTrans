# Kế hoạch tái kiến trúc upload TikTok trực tiếp và đặt lịch

- Feature: `tiktok-direct-scheduled-upload`
- Version: `0.1`
- Trạng thái: `IMPLEMENTED_RELEASE_0_1`
- Cơ sở: `ANALYSIS.md`, source hiện tại và tài liệu TikTok/2 repository tham khảo.
- Nguyên tắc: official-first; native schedule private chỉ đi qua spike và approval gate.

## 1. Kết quả mong muốn

Tách publishing thành một bounded workflow độc lập, cho phép đăng ngay, đặt lịch bền vững và gửi bản nháp. Hệ thống giữ đúng caption/hashtag người dùng đã duyệt, chống đăng trùng, phục hồi sau restart và có adapter thử nghiệm cho lịch native TikTok mà không làm rủi ro lan sang luồng chính thức.

## 2. Quyết định kiến trúc đề xuất

| ID | Quyết định | Lý do |
|---|---|---|
| ADR-01 | API TikTok chính thức là provider mặc định | Có OAuth, scope, status API/webhook và contract công khai |
| ADR-02 | Lịch production mặc định là `LOCAL_SCHEDULE` | API chính thức chưa công bố schedule field; vẫn dùng Direct Post hợp lệ |
| ADR-03 | API web private là adapter experimental | Hai repo chứng minh khả thi nhưng phụ thuộc session/private endpoint/anti-bot |
| ADR-04 | Publish có aggregate/store riêng | Tách trạng thái render khỏi trạng thái phát hành và cho phép retry/reconcile độc lập |
| ADR-05 | At-least-once worker + business idempotency | SQLite queue có thể claim lại; remote side effect cần kiểm soát trùng |
| ADR-06 | Không fallback provider âm thầm | Tránh thay đổi semantics, privacy hoặc thời điểm đăng mà người dùng không biết |
| ADR-07 | Snapshot caption/media tại lúc xác nhận | Bảo đảm dữ liệu duyệt là dữ liệu gửi đi |

## 3. Definition of Done

- BA: URD chốt actor, use case, BR/AC/NFR, late policy, provider policy và native-risk acceptance.
- DESIGN: port/adapter, state machine, schema, transaction, retry matrix, API và migration được review.
- DEV: official direct/local schedule/draft chạy qua publishing service mới; browser fallback còn hoạt động; native adapter chỉ triển khai sau gate.
- TEST: unit, contract, integration, crash recovery, duplicate prevention, security và UI tests có bằng chứng.
- DEPLOY: migration backup/rollback, flags, metrics, alert, canary và runbook được chuẩn bị; chưa deploy khi chưa có approval.

## 4. Work breakdown theo phase

### Phase 0 — Chốt nghiệp vụ và rủi ro

**Đầu vào:** `ANALYSIS.md`, yêu cầu người dùng.

**Việc làm:**

- Chốt release đầu gồm `DIRECT_NOW + LOCAL_SCHEDULE + INBOX_DRAFT` hay bắt buộc thêm native schedule.
- Chốt late policy: ví dụ trễ `<=15 phút` đăng tự động; `>15 phút` chuyển `NEEDS_REVIEW`.
- Chốt một hay nhiều account, timezone mặc định, retention video và cancel semantics.
- Lập decision record chấp nhận/không chấp nhận private web API.
- Viết URD, acceptance criteria và traceability skeleton.

**Đầu ra:**

- `BA_URD/tiktok-direct-scheduled-upload/URD.md`
- cập nhật `OPEN_QUESTIONS.md`, `REVIEW.md`, `TRACEABILITY.md`

**Gate G0:** người dùng duyệt phạm vi và rủi ro trước DEV.

### Phase 1 — Thiết kế domain và contract

**Việc làm:**

- Tạo enum/value object:
  - `PublishRequestMode`, `ScheduleMode`, `PublishProvider`, `PublishStatus`, `PublishPhase`;
  - `PublishIntent`, `CaptionSnapshot`, `ScheduleSpec`, `AccountCapability`.
- Định nghĩa `TikTokPublishingGateway`:
  - `get_capabilities(account_id)`;
  - `initialize_upload(intent)`;
  - `upload_chunks(session, media)`;
  - `submit(intent, session)`;
  - `fetch_status(remote_id)`;
  - `cancel_schedule(remote_id)` chỉ khi provider hỗ trợ.
- Định nghĩa error taxonomy: `AUTH`, `VALIDATION`, `RATE_LIMIT`, `TRANSIENT_PRE_SUBMIT`, `REMOTE_REJECTED`, `AMBIGUOUS_POST_SUBMIT`, `CAPABILITY_UNAVAILABLE`.
- Lập retry matrix cho từng phase/error; mọi retry hậu side-effect cần remote ID hoặc operator action.
- Thiết kế structured event/audit không chứa secret.

**Đầu ra:** `DEV/tiktok-direct-scheduled-upload/DESIGN.md`.

**Kiểm chứng:** review state transition đầy đủ; không có transition tự động từ ambiguous sang upload lại.

### Phase 2 — Persistence và migration

**Việc làm:**

- Thêm table `tiktok_publish_attempts` và index:
  - unique `idempotency_key`;
  - index `(status, scheduled_for_utc)`;
  - index `job_id`, `remote_publish_id`;
  - optimistic `version` hoặc atomic conditional update.
- Cân nhắc table `tiktok_accounts` chỉ lưu metadata/capability; secret nằm qua credential store.
- Viết repository với transaction cho create/claim/checkpoint/complete/cancel.
- Pin media còn publish attempt chưa terminal; cleanup chỉ xóa khi refcount bằng 0.
- Migration tương thích job cũ:
  - backfill job `status=scheduled` thành publish attempt `SCHEDULED_LOCAL`;
  - giữ đường đọc legacy một release;
  - marker migration để rerun an toàn.
- Backup DB trước migration và script kiểm tra số lượng trước/sau.

**Đầu ra:** schema/migration, repository tests, `db/MIGRATION_NOTES.md` nếu repo dùng SQL artifact.

**Gate G1:** migration chạy hai lần không lỗi; rollback code đọc được schema additive.

### Phase 3 — Publishing application service

**Việc làm:**

- Tạo `TikTokPublishingService` làm orchestration duy nhất.
- Endpoint create attempt nhận `job_id`, caption snapshot, mode, schedule, privacy/options và idempotency key.
- Validate:
  - job đã render xong, media tồn tại/đọc được;
  - caption tối đa theo provider bằng UTF-16 units khi cần;
  - privacy nằm trong creator capability;
  - schedule có timezone, hợp lệ và chưa quá hạn;
  - explicit consent/review flag trước Direct Post.
- Snapshot media hash/size và caption hash.
- Scheduler claim attempt atomic; không dùng `job_action=publish_tiktok` làm source of truth mới.
- Implement late policy và cancel lịch local trước claim.
- Persist checkpoint ngay sau mỗi remote response có identifier.

**Kiểm chứng:** tests cho double-submit, concurrent claim, restart recovery, late schedule và cancellation race.

### Phase 4 — Official Content Posting adapter

**Việc làm:**

- Refactor `TikTokPublisher` hiện có để implement gateway, giữ OAuth/token refresh.
- Giữ `FILE_UPLOAD`; cân nhắc `PULL_FROM_URL` chỉ khi có verified domain và media đã ở server theo guideline TikTok.
- Stream chunk 5–64 MiB; không nạp toàn file vào RAM; checkpoint byte progress.
- Luôn query creator info trước direct post và áp privacy/duration capability.
- Truyền đúng caption snapshot vào `post_info.title`; thêm contract tests có Unicode, dòng mới, hashtag và mention.
- Bổ sung các option còn thiếu nếu UI cho phép: AIGC, brand toggle, comment/duet/stitch, cover timestamp.
- Sau upload chỉ chuyển `SUBMITTED/PROCESSING_REMOTE`; không coi là `PUBLISHED` trước status/webhook.
- Reconciler poll có rate limit, exponential backoff và terminal timeout alert.
- Thiết kế webhook handler có signature verification/replay protection theo tài liệu chính thức trước khi bật.

**Gate G2:** official direct, local schedule và inbox draft pass integration với fake server; sandbox/live test chỉ trên account test.

### Phase 5 — API và UI workflow

**API đề xuất:**

- `POST /api/v1/jobs/{job_id}/tiktok-publishes`
- `GET /api/v1/tiktok-publishes/{attempt_id}`
- `POST /api/v1/tiktok-publishes/{attempt_id}/cancel`
- `POST /api/v1/tiktok-publishes/{attempt_id}/retry` chỉ cho transition được phép
- `GET /api/v1/tiktok/accounts/{account_id}/capabilities`
- webhook route riêng nếu được TikTok cấu hình.

**UI:**

- Sau preview: caption editor + counter, privacy/options, account, mode segmented control.
- `Đăng ngay` tạo attempt sau checkbox xác nhận; button disable sau click và dùng idempotency key ổn định.
- `Đặt lịch` hiển thị local timezone, UTC interpretation và loại lịch thực tế (`VidTrans` hoặc `TikTok native experimental`).
- Timeline riêng: `Chờ lịch → Đang upload → TikTok đang xử lý → Đã đăng`.
- Trạng thái `Cần đăng nhập lại`, `Cần kiểm tra`, `Có thể trùng` có action rõ.
- Không mô tả upload thành “đã đăng” khi mới chỉ nhận upload/publish ID.

**Kiểm chứng:** browser/API tests cho caption fidelity, double click, timezone, refresh/reload và responsive UI.

### Phase 6 — Spike adapter lịch native TikTok

**Điều kiện vào phase:** G0 chấp nhận rủi ro private API và có account test riêng.

**Spike không production:**

- Dùng DevTools/Playwright trên TikTok Studio với account test để xác minh request hiện hành; không cố bypass CAPTCHA.
- Xác minh endpoint, domain region, CSRF/signature/cookie cần thiết, schedule constraints và response IDs.
- Kiểm tra business-account requirement, edit/cancel/status endpoint và session refresh behavior.
- Viết protocol fixture đã redaction; không commit cookie/token/raw signed URL.
- So sánh với hai repo, nhưng không giả định endpoint/`X-Bogus` cũ còn hợp lệ.

**Tiêu chí go/no-go:**

- GO khi: legal/product chấp nhận; create schedule ổn định trên account test; có remote ID; có cách reconcile; secret handling đạt review; failure không tạo trùng.
- NO-GO khi: phải bypass anti-bot, không có reconciliation, endpoint đổi liên tục, hoặc account bị hạn chế.

**Nếu GO:** implement `WebSessionNativeScheduleAdapter` sau gateway, flag mặc định off, account allowlist, kill switch và canary tối đa một account.

**Nếu NO-GO:** release official direct + local schedule; browser Studio là fallback cho native scheduling thủ công.

### Phase 7 — Test và security review

**Unit/contract tests:**

- State transition hợp lệ/không hợp lệ.
- Idempotency cùng key và concurrent create.
- Caption Unicode/newline/hashtag/mention truyền nguyên vẹn.
- Chunk boundary: nhỏ hơn 5 MiB, 64 MiB, tail, file lớn và short read.
- Schedule UTC/timezone, quá khứ, late policy, max horizon.
- OAuth expiry/refresh, revoked scope, privacy mismatch, 429/5xx.
- Timeout tại init/upload/finalize/submit/status và retry matrix.
- Ambiguous response không gọi create-post lần hai.
- Media cleanup giữ file còn schedule.

**Integration/recovery tests:**

- Restart trước claim, giữa chunk, sau nhận remote ID và sau submit timeout.
- Hai worker claim cùng attempt.
- Migration từ scheduled job cũ.
- Official fake API + webhook duplicate/out-of-order.
- Browser fallback không nhận path/file/caption ngoài job được phép.

**Security tests:**

- Secret redaction trong log/API/exception.
- SSRF/open redirect/host validation cho upload URL và callback.
- CSRF state, webhook signature/replay, account authorization.
- Rate limit và request body limits.

**Đầu ra:** `TESTCASES.md`, `TEST_REPORT.md`, `TRACEABILITY.md`, security findings trong `REVIEW.md`.

### Phase 8 — Rollout và rollback

**Flags đề xuất:**

- `TIKTOK_PUBLISHING_V2_ENABLED`
- `TIKTOK_LOCAL_SCHEDULE_V2_ENABLED`
- `TIKTOK_NATIVE_SCHEDULE_ENABLED` mặc định `false`
- `TIKTOK_NATIVE_SCHEDULE_ACCOUNT_ALLOWLIST`
- `TIKTOK_PUBLISHING_V2_READ_ONLY` cho shadow/reconciliation.

**Rollout:**

1. Deploy schema additive, code vẫn dùng flow cũ.
2. Shadow-read/migrate lịch cũ và so sánh counts.
3. Bật V2 cho inbox draft/account test.
4. Bật direct post canary.
5. Bật local schedule canary qua một chu kỳ restart.
6. Mở rộng official flow sau khi metrics ổn định.
7. Native schedule chỉ canary sau G3 riêng.

**Rollback:**

- Tắt create V2 nhưng giữ reconciler đọc các attempt đã submit.
- Không rollback DB destructive; schema additive được giữ.
- Attempt `SCHEDULED_LOCAL` chưa claim có thể chuyển về legacy chỉ bằng migration có idempotency và audit.
- Attempt có remote ID tiếp tục reconcile; tuyệt đối không requeue sang flow cũ.
- Kill switch native không xóa lịch remote; đưa chúng vào danh sách theo dõi/operator action.

**Observability/alert:**

- Alert khi auth sắp hết hạn, success rate giảm, 429 tăng, schedule lag vượt ngưỡng, reconciliation quá lâu, ambiguous attempt hoặc native adapter lỗi liên tiếp.
- Dashboard tách provider/mode/account đã mask; không gắn secret/caption vào label.

## 5. Ma trận provider và mode

| Mode | Official API | Web private | Browser Studio |
|---|---|---|---|
| Đăng ngay | Primary | Không cần ở release đầu | Fallback thủ công |
| Lịch nội bộ | Primary khi đến hạn | Không cần | Fallback thủ công |
| Lịch native TikTok | Không thấy contract chính thức | Experimental sau spike | Người dùng thao tác trực tiếp |
| Upload draft | Primary | Không cần | Có thể tiếp tục thủ công |
| Reconcile | Status API/webhook | Phải chứng minh trong spike | Xác nhận thủ công |

## 6. Dependency map

```text
G0 BA approval
  -> Phase 1 domain/design
  -> Phase 2 persistence
  -> Phase 3 application service
  -> Phase 4 official adapter
  -> Phase 5 API/UI
  -> Phase 7 test
  -> Phase 8 official rollout

G0 private-risk approval
  -> Phase 6 native spike
  -> G3 go/no-go
  -> native adapter test/canary (nếu GO)
```

Phase 6 không chặn release official direct + local schedule.

## 7. Ước lượng tương đối

| Hạng mục | Size | Ghi chú |
|---|---:|---|
| BA + design | M | Nhiều quyết định failure/idempotency |
| Persistence/migration | M | Schema additive nhưng cần legacy migration |
| Publishing service + official adapter | L | Tách code và recovery paths |
| API/UI | M | Workflow review/status/cancel |
| Test/security/observability | L | Phần bắt buộc do remote side effect |
| Native schedule spike | L/Unknown | Phụ thuộc TikTok web hiện hành và account test |

Không chốt ngày công trước khi đóng Q01–Q04 và hoàn tất spike native.

## 8. Tự review plan

- Bao phủ create → schedule → claim → upload → submit → reconcile → cancel/rollback.
- Tách rõ trạng thái render video và trạng thái publish.
- Không coi hai repo private API là dependency production mặc định.
- Có checkpoint/idempotency cho remote side effect và xử lý kết quả mơ hồ.
- Có migration cho lịch cũ, pin media và rollback không destructive.
- Có gate riêng cho legal/security/native capability.
- Khoảng trống còn lại được ghi trong `OPEN_QUESTIONS.md`; chưa được tự suy đoán thành requirement.

## 9. Approval gate

Chỉ bắt đầu viết URD chi tiết/DEV khi người dùng duyệt plan và trả lời tối thiểu:

1. Release đầu có bắt buộc native schedule hay chấp nhận local schedule?
2. Có cho phép thử nghiệm API web private trên account test không?
3. Chính sách khi worker đăng trễ là gì?

## 10. Implementation record

- Người dùng duyệt bắt đầu ngày `2026-10-10`.
- Release 0.1 đã triển khai `DIRECT_NOW`, `LOCAL_SCHEDULE`, `INBOX_DRAFT`, persistence, idempotency, reconciliation, API/UI và runbook.
- Late policy 0.1: đăng khi worker hoạt động lại và lưu `schedule_lag_seconds`.
- Native schedule private chưa triển khai; giữ nguyên gate G3.
