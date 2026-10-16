# ANALYSIS — Tối ưu đăng đa nền tảng và quan hệ 1:1 để thương mại hóa

- Feature: `content-relationship-commerce`
- Phiên bản: `0.1-draft`
- Ngày phân tích: `2026-10-16`
- Trạng thái: `IN_REVIEW`
- Nguồn: yêu cầu người dùng ngày 2026-10-16, kiến trúc và hồ sơ tính năng hiện có của VidTrans

## 1. Vấn đề cần giải quyết

VidTrans đã có pipeline tạo video, social connection, lịch đăng và lõi publishing đa nền tảng. Tuy nhiên, để biến năng lực này thành doanh thu bền vững còn thiếu hai vòng lặp sản phẩm:

1. **Content Operations:** biến một video nguồn thành nhiều biến thể phù hợp từng nền tảng, duyệt một lần, xếp lịch, đăng, theo dõi và tái sử dụng có hệ thống.
2. **Relationship Commerce:** biến tương tác của từng người xem thành một hồ sơ quan hệ 1:1 có consent, lịch sử, nhu cầu, hành động tiếp theo, offer và kết quả doanh thu.

Nếu chỉ tối ưu số lượng bài đăng, sản phẩm dễ tạo thêm nội dung nhưng không chứng minh được doanh thu. Nếu chỉ làm CRM, người dùng lại phải nhập liệu thủ công và thiếu nguồn tương tác. Hai vòng lặp phải dùng chung campaign, contact, attribution và analytics.

## 2. Mục tiêu đo được

### Mục tiêu vận hành nội dung

- Giảm thời gian từ video hoàn tất đến khi tạo xong lịch đăng đa nền tảng ít nhất 60% so với thao tác thủ công ban đầu.
- Tỷ lệ publish thành công sau reconcile đạt tối thiểu 95%, không tính lỗi do tài khoản bị thu hồi quyền hoặc provider outage.
- Một asset nguồn tạo được tối thiểu 3 biến thể bài đăng đã duyệt mà không nhân bản media pipeline.
- Không tạo bài đăng trùng khi double-click, retry, restart hoặc timeout từ provider.

### Mục tiêu quan hệ và thương mại hóa

- 100% contact có nguồn tạo, owner, trạng thái consent và lịch sử thay đổi được truy vết.
- Không tự động gộp hai danh tính chỉ dựa trên tên hiển thị, username tương tự hoặc suy đoán AI.
- Đo được funnel: `post → interaction/click → captured contact → qualified → offer → paid/retained`.
- Pilot chứng minh ít nhất một ICP có conversion sang trả phí và gross margin dương trước khi mở rộng automation.

## 3. AS-IS

### CONFIRMED từ repository

- VidTrans là modular monolith với pipeline download/upload → OCR/ASR → dịch → TTS/render → artifact.
- Có publish attempt/state machine, idempotency, local scheduling, reconciliation và provider capability cho nhiều nền tảng.
- Có thiết kế kết nối social theo user, RBAC/customer billing, tenant/membership và usage ledger.
- Có durable outbox sang n8n để làm notification, CRM và automation ngoại vi mà không chặn media pipeline.
- Chưa thấy bounded context hoàn chỉnh cho content calendar/campaign, unified contact, conversation timeline, offer và revenue attribution.

### ASSUMPTION cần review

- “Quan hệ 1-1 với 1 user” được hiểu là mỗi audience member/lead/customer có một hồ sơ quan hệ riêng, không phải chỉ là quan hệ giữa tài khoản VidTrans và một tài khoản social.
- MVP ưu tiên creator hoặc đội content nhỏ bán dịch vụ/sản phẩm số; agency nhiều khách hàng là bước sau.
- Phản hồi 1:1 có thể bắt đầu từ form/link/QR và thao tác thủ công; tích hợp comment/DM tự động chỉ bật theo capability và chính sách của từng nền tảng.

## 4. TO-BE

```text
Asset/Video
   ↓
Campaign → Content variants → Review → Calendar → Publish attempts
   ↓                                             ↓
CTA / tracked link                          Post performance
   ↓                                             ↓
Contact capture ← Interaction/Event ← Attribution
   ↓
Contact 360 → Conversation/Task → Qualification → Offer → Order/Subscription
   ↑______________________________________________________________|
                       feedback về nội dung tạo doanh thu
```

### 4.1 Content Operations

- **Content asset:** video gốc và artifact; không copy binary cho mỗi platform.
- **Campaign:** mục tiêu, ICP, offer, CTA, thời gian, owner và ngân sách/target.
- **Content variant:** caption, hook, hashtag, thumbnail/cover, CTA và option riêng theo capability platform.
- **Review gate:** draft → needs review → approved → scheduled; thay đổi sau duyệt tạo revision mới.
- **Calendar:** xem theo ngày/tuần/campaign/platform; cảnh báo trùng lịch, quá tần suất và tài khoản mất kết nối.
- **Publish orchestration:** dùng lõi publish hiện có; giữ idempotency, state machine và reconcile làm source of truth.
- **Feedback loop:** kết quả bài đăng và click/conversion được quy về variant/campaign, không chỉ tổng theo platform.

### 4.2 Relationship 1:1

- **Contact profile:** ID nội bộ, channel identities, nguồn, owner, lifecycle stage, tags, consent và preferences.
- **Identity resolution:** khóa xác minh như email/phone hoặc account ID provider trong đúng tenant; match mơ hồ vào hàng chờ review.
- **Timeline:** capture, click, form submit, note, message metadata, task, offer và order; không lưu nội dung nhạy cảm thừa.
- **Inbox/task:** giao owner, SLA phản hồi, next action, reminder và trạng thái xử lý.
- **Segmentation:** rule theo source/campaign/engagement/lifecycle; AI chỉ đề xuất, user duyệt trước khi gửi hàng loạt.
- **Consent:** phân biệt transactional, chăm sóc và marketing; unsubscribe/suppression có hiệu lực trên mọi automation liên quan.

### 4.3 Commerce và attribution

- **Offer:** dịch vụ, sản phẩm số, membership hoặc gói credit; có giá, currency, thời hạn và CTA.
- **Tracked touchpoint:** link/landing/form/coupon gắn `campaign_id`, `variant_id`, `contact_id` khi có consent.
- **Conversion:** lead qualified, offer accepted, checkout started, paid, refunded, renewed.
- **Attribution MVP:** last-known campaign/variant trước conversion; lưu toàn bộ touchpoint để nâng cấp multi-touch sau.
- **Revenue view:** doanh thu, refund, direct variable cost và contribution margin theo campaign/offer/cohort.

## 5. Actor và quyền

| Actor | Quyền chính | Giới hạn |
|---|---|---|
| `OWNER/ADMIN` | Quản lý campaign, contact, offer, automation, billing | Thao tác nhạy cảm có audit; không xem secret thô |
| `CREATOR/MARKETER` | Tạo nội dung, duyệt, lên lịch, xem analytics | Chỉ tenant/account được cấp quyền |
| `SALES/CS` | Xem contact được giao, cập nhật timeline, gửi offer | Không tự export toàn bộ contact; marketing cần consent |
| `CONTACT` | Gửi form, chọn consent, nhận offer, unsubscribe | Không truy cập dữ liệu contact khác |
| `WORKER/INTEGRATION` | Publish, reconcile, ingest event, gửi automation | Service identity và scope tối thiểu; idempotent |

## 6. Phạm vi MVP và ngoài phạm vi

### MVP

- Campaign, content variant, approval và calendar dùng lõi publish hiện có.
- Tracked link/form để tạo contact và attribution đáng tin cậy.
- Contact 360, timeline, task, lifecycle, consent và suppression.
- Offer cơ bản và ghi nhận paid/refund qua billing/event contract hiện có.
- Dashboard content-to-revenue và export có kiểm soát.
- Automation tối thiểu: follow-up task, nhắc SLA, nurture có consent, dừng ngay khi paid/unsubscribed.

### Sau MVP

- Omnichannel inbox đầy đủ, comment/DM ingestion theo từng provider.
- Lead scoring bằng ML, multi-touch attribution, A/B tự động, recommendation engine.
- Agency workspace, nhiều brand, approval nhiều cấp, white-label và reseller.

### Ngoài phạm vi

- Scrape danh tính, mua danh sách lead hoặc gửi cold message hàng loạt không có consent.
- Tự động gộp contact mơ hồ hoặc chia sẻ contact giữa tenant.
- Cam kết API DM/comment tồn tại trên mọi platform.
- Cho n8n làm source of truth cho consent, order, billing hoặc publish state.

## 7. Data domain đề xuất

| Aggregate | Mục đích |
|---|---|
| `campaigns` | Mục tiêu, ICP, offer, CTA, thời gian, owner |
| `content_variants` | Snapshot nội dung theo platform và revision duyệt |
| `calendar_slots` | Thời điểm dự kiến, timezone, conflict/policy result |
| `contacts` | Hồ sơ quan hệ thuộc tenant |
| `contact_identities` | ID theo channel; unique trong tenant + provider |
| `consent_records` | Scope, trạng thái, nguồn bằng chứng, thời điểm |
| `relationship_events` | Timeline append-only đã lọc PII |
| `tasks` | Owner, SLA, next action, trạng thái |
| `offers` | Sản phẩm/dịch vụ, giá, CTA và điều kiện |
| `conversion_events` | Qualified/accepted/paid/refund/renewal |
| `attribution_touchpoints` | Liên kết post/variant/campaign/contact |

Mọi aggregate nghiệp vụ phải có `tenant_id`; quyền lấy từ security context đã xác minh, không tin `tenant_id`, `user_id`, role hoặc owner do client gửi.

## 8. KPI và dashboard

### Content efficiency

- Time-to-first-schedule, thời gian duyệt, số variant/asset, publish success/retry rate.
- Tỷ lệ lịch bị trễ, connection health, tỷ lệ bài cần sửa sau review.

### Relationship

- Captured contacts, consent rate, first-response time, overdue tasks, qualified rate.
- Tỷ lệ contact không định danh được hoặc merge cần review.

### Commercial

- Click-to-contact, contact-to-qualified, qualified-to-paid, repeat purchase/renewal.
- Revenue, refund, ARPU, CAC proxy, variable cost và contribution margin theo campaign.

## 9. Rủi ro và kiểm soát

| Rủi ro | Mức độ | Kiểm soát |
|---|---|---|
| Spam hoặc vi phạm consent | Critical | Explicit opt-in, suppression, frequency cap, audit, kill switch |
| Gộp sai danh tính | High | Deterministic verified keys; ambiguous review; reversible merge |
| Cross-tenant data leak/IDOR | Critical | Object authorization, tenant-scoped repository, negative tests |
| Đăng trùng hoặc gửi offer trùng | High | Idempotency key, outbox, reconcile, remote ID checkpoint |
| API platform thay đổi | High | Capability registry, adapter contract, graceful degradation |
| Attribution bị thổi phồng | Medium | Định nghĩa model rõ, lưu raw touchpoint đã tối thiểu hóa, không suy diễn conversion |
| Automation làm mất tính cá nhân | Medium | Human approval ở bước có tác động cao, rate limit, next-best-action thay vì auto-send mặc định |

## 10. Câu hỏi cần người dùng xác nhận

1. ICP đầu tiên là creator bán sản phẩm số, người bán dịch vụ 1:1, shop hay agency?
2. “1 user” là người xem/lead/customer hay là một khách hàng trả phí của VidTrans?
3. Kênh chốt đơn ưu tiên: landing/form, Zalo, email, Messenger, TikTok/Instagram DM hay cuộc gọi?
4. Sản phẩm thương mại đầu tiên là subscription VidTrans, dịch vụ làm nội dung, sản phẩm số hay affiliate?
5. Có cho hệ thống tự gửi follow-up hay chỉ tạo gợi ý/task để người dùng duyệt?

## 11. Tiêu chí nghiệm thu cấp tính năng

- Một asset tạo nhiều variant, duyệt và lên lịch đa nền tảng mà không copy binary hoặc tạo publish trùng.
- Một contact được tạo từ CTA, có consent, timeline, owner và next action; không thấy dữ liệu tenant khác.
- Một conversion paid/refund truy ngược được tới contact, offer, campaign và content variant theo model attribution đã công bố.
- Unsubscribe/revoke consent chặn mọi automation marketing kế tiếp trong SLA xác định.
- Dashboard phân biệt rõ publish metrics, relationship metrics và doanh thu thực nhận.
