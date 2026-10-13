# Coding plan — Multi-platform social publishing

- Feature: `multi-platform-publishing`
- Ngày lập kế hoạch: `2026-10-13`
- Phạm vi: mở rộng VidTrans sang các nền tảng ngoài YouTube và Facebook.
- Nền tảng đã có trong codebase: TikTok Direct Post, inbox draft, lịch nội bộ và reconciliation.

## Trạng thái triển khai

- Phase 0: **hoàn tất** — domain contract, capability model, provider protocol/registry
  và fake-provider test harness.
- Phase 1: **hoàn tất** — SQLite store, idempotency, atomic schedule claim,
  application service, reconciliation scheduler và REST API dùng chung.
- Phase 2: **hoàn tất code và automated tests** — Instagram Reels adapter, OAuth/account
  discovery và signed media delivery URL. Còn staging smoke test sau khi có Meta credentials.
- Phase 3: **hoàn tất** — UI multi-platform cho Instagram account, direct/local schedule,
  share-to-feed, trạng thái, reconciliation polling và cancel.
- Phase 4: **hoàn tất** — TikTok compatibility adapter, status normalization, generic
  registry/API và migration idempotent cho lịch sử terminal. Attempt TikTok đang hoạt động
  tiếp tục do scheduler cũ quản lý để tránh double side effect.
- Phase 5: **đang triển khai** — Threads adapter/OAuth/video publish/reconcile đã hoàn
  tất automated tests; LinkedIn và Pinterest là hai nhánh tiếp theo.
- Xác minh ngày `2026-10-13`: 39 targeted tests pass; full suite chạy 264 tests. Còn
  1 import error có sẵn (`infrastructure.douyin_qr_auth` bị thiếu); auth tamper test fail
  ngẫu nhiên một lần nhưng pass khi chạy lại riêng.

## 1. Mục tiêu

Tạo một lõi publishing dùng chung để một video đã render có thể được duyệt, lên lịch,
đăng và theo dõi trạng thái trên nhiều nền tảng mà không nhân bản business logic theo
từng provider.

Thứ tự triển khai:

1. Instagram Reels — MVP đầu tiên vì phù hợp trực tiếp với video dọc của VidTrans.
2. Threads — tận dụng hệ sinh thái Meta và hỗ trợ nội dung text/video ngắn.
3. LinkedIn — video post cho tài khoản cá nhân hoặc organization đã được cấp quyền.
4. Pinterest — video Pin, yêu cầu board và cover image.
5. X — triển khai sau cùng vì cần xác nhận gói API, quota và chi phí thực tế.

YouTube và Facebook không nằm trong feature này. TikTok tiếp tục hoạt động trong giai
đoạn chuyển đổi và sẽ được đưa vào contract chung sau khi Instagram MVP ổn định.

## 2. Nguyên tắc kiến trúc

- API route chỉ validate input, resolve job/media và gọi application service.
- Application service quản lý intent, lịch, idempotency, retry và state machine.
- Adapter provider quản lý OAuth, upload, submit và lấy trạng thái từ API bên ngoài.
- Secret/token không nằm trong publish payload hoặc response công khai.
- Mỗi nền tảng có capability riêng; UI không hiển thị option provider không hỗ trợ.
- Có remote ID thì không tự tạo lại bài đăng khi kết quả không rõ ràng.
- Lịch đăng dùng scheduler nội bộ trừ khi provider có native scheduling đã được xác minh.
- TikTok API/UI hiện tại phải tiếp tục chạy trong suốt quá trình migration.

## 3. Domain contract dùng chung

### Platform

`TIKTOK | INSTAGRAM | THREADS | LINKEDIN | PINTEREST | X`

### Publish mode

`DIRECT_NOW | SCHEDULE_LOCAL | DRAFT`

`DRAFT` chỉ khả dụng khi provider khai báo hỗ trợ.

### Status

```text
READY -> SCHEDULED_LOCAL -> CLAIMED -> UPLOADING -> PROCESSING_REMOTE
      -> SUBMITTING -> PUBLISHED

READY/SCHEDULED_LOCAL -> CANCELLED
UPLOADING/SUBMITTING -> FAILED | NEEDS_RECONCILIATION
PROCESSING_REMOTE -> PUBLISHED | FAILED | NEEDS_RECONCILIATION
```

### Capability

```python
PublisherCapabilities(
    supports_video=True,
    supports_text=False,
    supports_draft=False,
    supports_remote_status=True,
    supports_thumbnail=False,
    supports_first_comment=False,
    caption_limit=2200,
    schedule_horizon_days=365,
)
```

Provider chịu trách nhiệm validate giới hạn thay đổi theo nền tảng; client chỉ dùng
capability để hướng dẫn người dùng, không thay thế validation phía server.

## 4. Persistence

Thêm bảng additive `social_publish_attempts` trong `work/jobs.sqlite3`:

| Cột | Mục đích |
|---|---|
| `attempt_id` | ID nội bộ |
| `job_id` | Video nguồn |
| `platform` | Provider đích |
| `account_id` | Tài khoản đã kết nối |
| `mode` | Direct/schedule/draft |
| `status` | Trạng thái chuẩn hóa |
| `scheduled_for` | Thời gian UTC của lịch nội bộ |
| `idempotency_key` | Chống đăng trùng |
| `remote_media_id` | Checkpoint sau khi init/upload |
| `remote_post_id` | ID bài đăng sau submit |
| `payload_json` | Caption, option và metadata không nhạy cảm |
| `status_detail_json` | Trạng thái đã lọc từ provider |
| `error_code`, `error_message` | Lỗi an toàn cho UI |
| `claimed_at`, `created_at`, `updated_at` | Điều phối và audit |

Unique index đề xuất:

```text
UNIQUE(platform, account_id, idempotency_key)
INDEX(status, scheduled_for)
INDEX(job_id, platform, created_at DESC)
```

Không xóa hoặc đổi schema `tiktok_publish_attempts` trong MVP. Sau khi parity test đạt,
viết migration một lần để copy lịch sử TikTok sang bảng chung; migration phải idempotent.

## 5. Cấu trúc code dự kiến

```text
backend/
├── domain/
│   └── social_publishing.py          # enum, capability, request/result, protocol
├── application/
│   ├── social_publishing.py          # create/run/reconcile/cancel
│   ├── social_publish_scheduler.py   # atomic due claim + reconciliation
│   └── social_publish_routes.py      # REST API v1
├── infrastructure/
│   ├── social_publish_store.py       # SQLite repository
│   ├── social_credentials.py         # token store/refresh boundary
│   └── publishers/
│       ├── registry.py
│       ├── instagram.py
│       ├── threads.py
│       ├── linkedin.py
│       ├── pinterest.py
│       └── x.py
├── frontend/
│   └── js/social-publishing.js       # modal, account, schedule, progress
└── tests/
    ├── test_social_publish_store.py
    ├── test_social_publishing.py
    ├── test_social_publish_routes.py
    └── test_*_publisher.py
```

## 6. API contract

```text
GET    /api/v1/social-platforms
GET    /api/v1/social-accounts
POST   /api/v1/social-accounts/{platform}/authorize
GET    /api/v1/social-accounts/{platform}/callback

POST   /api/v1/jobs/{job_id}/social-publishes
GET    /api/v1/jobs/{job_id}/social-publishes
GET    /api/v1/social-publishes/{attempt_id}
POST   /api/v1/social-publishes/{attempt_id}/cancel
POST   /api/v1/social-publishes/{attempt_id}/refresh
```

Create request:

```json
{
  "platform": "INSTAGRAM",
  "account_id": "creator-one",
  "mode": "DIRECT_NOW",
  "caption": "Caption đã duyệt",
  "scheduled_for": null,
  "timezone": "Asia/Bangkok",
  "options": {
    "share_to_feed": true
  },
  "reviewed": true,
  "idempotency_key": "client-generated-key"
}
```

Response công khai không chứa filesystem path, access token, refresh token, upload URL
hoặc raw provider response.

## 7. Phân kỳ triển khai

### Phase 0 — Contract và test harness

- Tạo domain types, provider protocol và registry.
- Tạo fake publisher để test mọi transition mà không gọi mạng.
- Định nghĩa mapping trạng thái provider -> trạng thái chuẩn.
- Viết contract tests bắt buộc cho mọi adapter.

Gate hoàn thành: domain/provider contract độc lập với TikTok và không sửa behavior cũ.

### Phase 1 — Store, service và scheduler dùng chung

- Tạo `SQLiteSocialPublishStore` với schema/index additive.
- Implement create, idempotency, atomic `claim_due`, cancel và reconciliation queue.
- Implement `SocialPublishingService` và scheduler async dùng `asyncio.to_thread`.
- Thêm workflow event: `publish.requested`, `publish.uploading`,
  `publish.submitted`, `publish.completed`, `publish.failed`.

Gate hoàn thành: unit test cho restart, double click, concurrent claim, cancel và lỗi mơ hồ.

### Phase 2 — Instagram Reels MVP

- OAuth/account discovery cho tài khoản Instagram đủ điều kiện.
- Tạo media container, theo dõi processing, gọi publish và lưu remote IDs.
- Cung cấp video qua URL HTTPS tạm thời có chữ ký, thời hạn ngắn; không public toàn bộ
  thư mục output.
- Hỗ trợ caption và `share_to_feed`; lịch chạy bằng scheduler nội bộ.
- Reconcile container/post sau restart hoặc timeout.

Gate hoàn thành: sandbox account đăng được Reel, timeout không tạo bài trùng, token hết
hạn hiển thị lỗi có thể xử lý.

### Phase 3 — UI multi-platform

- Thay khối TikTok-only bằng danh sách platform/account có capability-driven controls.
- Cho phép chọn nhiều đích nhưng tạo attempt riêng cho từng platform.
- Hiển thị trạng thái độc lập; một nền tảng lỗi không làm các nền tảng khác thất bại.
- Preview caption riêng theo platform và bắt buộc xác nhận trước khi đăng.
- Giữ nút TikTok cũ dưới feature flag cho đến khi migration hoàn tất.

Gate hoàn thành: keyboard/mobile accessibility, polling dừng ở terminal state và không
hiển thị `SUBMITTED/PROCESSING_REMOTE` như đã đăng thành công.

### Phase 4 — Đưa TikTok vào lõi chung

- Viết adapter bọc `TikTokPublisher`; không viết lại upload implementation đang chạy.
- Chạy parity test direct, draft, schedule, progress và reconciliation.
- Migrate lịch sử theo batch idempotent; route cũ trở thành compatibility facade.
- Chỉ xóa code/store cũ ở release sau và sau khi có backup/rollback plan.

Gate hoàn thành: toàn bộ test TikTok hiện tại vẫn pass qua contract mới.

### Phase 5 — Threads, LinkedIn, Pinterest

- Threads: text/video container, publish và status mapping.
- LinkedIn: initialize multipart upload, finalize video rồi tạo Post; version API để ở
  config thay vì hard-code.
- Pinterest: chọn board, register/upload/poll media, tạo video Pin; bổ sung cover image.
- Mỗi adapter phải có OAuth scopes tối thiểu và feature flag riêng.

Gate hoàn thành: mỗi provider đạt contract test, integration smoke test và app review
trước khi bật production.

### Phase 6 — X và analytics

- Chỉ bắt đầu sau khi xác nhận API plan/quota/chi phí và quyền media upload.
- Thêm normalized metrics: views, likes, comments, shares, saves nếu API cho phép.
- Poll theo backoff; không làm chậm scheduler đăng bài.

## 8. Security và vận hành

- Mã hóa token at rest; log chỉ account alias và request correlation ID.
- Upload URL có credential phải được redaction khỏi log và database.
- Egress allowlist theo provider; chỉ nhận HTTPS callback/remote media URL.
- OAuth state dùng một lần, có TTL và ràng buộc tenant/user.
- Rate limit theo account/platform; retry exponential backoff với jitter.
- Không retry submit khi đã có `remote_media_id`/`remote_post_id`; chuyển reconcile.
- Webhook phải verify signature; polling là fallback.
- Feature flags: `VIDTRANS_SOCIAL_PUBLISHING`, `VIDTRANS_INSTAGRAM_PUBLISHING`, ...
- Metrics: success rate, phase latency, uploaded bytes, retry count, schedule lag,
  reconciliation age, auth expiry và duplicate prevented.

## 9. Test matrix tối thiểu

| Nhóm | Ca kiểm thử |
|---|---|
| Validation | platform/mode/caption/media/account/schedule không hợp lệ |
| Idempotency | double click, retry HTTP, restart worker |
| Concurrency | hai scheduler chỉ một worker claim attempt |
| Upload | single/multipart, progress, timeout, provider 4xx/5xx |
| Side effect | lỗi trước init được retry; lỗi sau remote ID chỉ reconcile |
| OAuth | state sai/hết hạn, scope thiếu, refresh thất bại |
| Security | API/log không lộ token, path, upload URL hoặc raw response |
| Compatibility | TikTok routes/UI/tests cũ tiếp tục hoạt động |
| UI | multi-account, multi-platform, cancel, refresh, terminal states |

## 10. Thứ tự pull request

1. `social-publishing-domain-store`
2. `social-publishing-service-api`
3. `instagram-reels-adapter`
4. `social-publishing-ui`
5. `tiktok-adapter-migration`
6. `threads-linkedin-pinterest-adapters`
7. `x-adapter-and-social-analytics`

Mỗi PR phải có test, tài liệu cấu hình `.env.example`, migration/rollback note và không
trộn nhiều provider chưa ổn định trong cùng một release.

## 11. Definition of Done cho MVP

- Người dùng kết nối được một Instagram account hợp lệ.
- Chọn video completed, chỉnh caption, đăng ngay hoặc đặt lịch theo timezone.
- Mỗi thao tác tạo một attempt idempotent và có trạng thái riêng.
- Restart server không mất lịch và không đăng trùng.
- Remote timeout/lỗi mơ hồ được reconcile an toàn.
- UI phân biệt rõ đang upload, đang xử lý, đã đăng và thất bại.
- TikTok hiện tại không regression.
- Unit/integration tests pass và có smoke-test checklist cho môi trường staging.

