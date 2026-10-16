# PLAN — Content Operations, quan hệ 1:1 và thương mại hóa

- Feature: `content-relationship-commerce`
- Version: `0.1`
- Phạm vi: tối ưu quy trình đăng đa nền tảng, xây quan hệ 1:1 có consent và nối tới offer/doanh thu
- Nguồn yêu cầu: người dùng ngày `2026-10-16`; `ARCHITECTURE.md`; các hồ sơ multi-platform publishing, social connection, multi-tenant và billing hiện có
- Trạng thái: `IN_REVIEW`
- Approval record: _Chưa có_

## 1. Chiến lược triển khai

Không xây lại social publishing. Tận dụng publish attempt, scheduler, capability registry, social connection, tenant/RBAC, billing và n8n outbox hiện có; bổ sung ba bounded context:

1. **Content Operations:** campaign → variant → approval → calendar → publish.
2. **Relationship CRM:** contact → consent → timeline → task/conversation → lifecycle.
3. **Commerce Attribution:** offer → touchpoint → conversion → revenue/margin.

Thứ tự ưu tiên là đo lường trước, tối ưu workflow sau, automation cuối cùng. Mỗi phase phải chứng minh được chỉ số đầu ra trước khi mở rộng.

## 2. Roadmap 12 tuần đề xuất

| Phase | Thời lượng | Phạm vi | Exit criteria |
|---|---:|---|---|
| 0. ICP + baseline | 1 tuần | Chốt ICP, offer, kênh chốt đơn, đo thời gian đăng hiện tại và funnel hiện tại | Có baseline, north-star metric, data dictionary và 5–10 pilot users |
| 1. Content Operations MVP | 2–3 tuần | Campaign, variant/revision, review, calendar, batch schedule, conflict check | Một asset lên lịch ≥3 platform; giảm ≥40% thao tác; không publish trùng |
| 2. Contact 360 + consent | 2–3 tuần | Tracked link/form, contact identity, timeline, owner, task, consent/suppression | Lead đi từ post tới contact; cross-tenant/consent tests pass |
| 3. Offer + conversion | 2 tuần | Offer, CTA, checkout/payment event, paid/refund, attribution MVP | Truy ngược paid tới contact/campaign/variant; reconciliation pass |
| 4. Automation có kiểm soát | 2 tuần | SLA reminder, next-best-action, nurture có duyệt/consent, stop conditions | Không gửi trùng; unsubscribe/paid dừng đúng workflow |
| 5. Pilot + pricing | 2 tuần | Dashboard content-to-revenue, cohort, cost/margin, pricing experiment | Có conversion trả phí và contribution margin dương ở ít nhất một cohort |

## 3. Luồng vận hành TO-BE

### 3.1 Một nội dung → nhiều nền tảng

1. Chọn asset đã render hoặc upload nguồn.
2. Tạo campaign: mục tiêu, ICP, offer, CTA, khoảng thời gian.
3. Hệ thống sinh draft variant theo từng platform capability.
4. User chỉnh và duyệt; hệ thống khóa revision đã duyệt.
5. Calendar đề xuất slot theo timezone, frequency cap và xung đột tài khoản.
6. Tạo publish attempt từ snapshot bất biến; scheduler/reconcile hiện có thực thi.
7. Thu performance/touchpoint về campaign và đưa insight vào vòng nội dung kế tiếp.

### 3.2 Một tương tác → một quan hệ → một giao dịch

1. Người xem bấm tracked CTA hoặc gửi form/QR.
2. Hệ thống tạo/tìm contact bằng identity đã xác minh trong đúng tenant.
3. Ghi consent, source, campaign, variant và event đầu tiên.
4. Giao owner và next action theo SLA; AI có thể đề xuất tóm tắt/hành động nhưng không tự gửi mặc định.
5. Owner qualify contact, ghi nhu cầu và gửi offer phù hợp.
6. Payment/order event ghi paid/refund idempotent.
7. Dashboard cập nhật funnel, revenue và margin; workflow nurture dừng khi paid hoặc unsubscribe.

## 4. Backlog theo epic

### EPIC A — Instrumentation và campaign foundation (P0)

- Chuẩn hóa `campaign_id`, `variant_id`, `offer_id`, `contact_id` và correlation ID.
- Data dictionary cho impression/click/capture/qualified/paid/refund.
- Campaign CRUD, owner, mục tiêu, ICP, CTA và trạng thái.
- Không đưa PII vào metric label, URL công khai hoặc log.

### EPIC B — Content variant và calendar (P0)

- Variant theo provider capability; caption/hook/CTA/option riêng từng platform.
- Revision + approval state; audit người duyệt và thời điểm.
- Calendar ngày/tuần, timezone, bulk move, conflict/frequency rule.
- Batch create publish intents qua application service hiện có.
- Preview validation; provider failure không làm mất lịch/variant.

### EPIC C — Unified contact và consent (P0)

- Contact, channel identity, owner, lifecycle, tags và suppression.
- Deterministic identity match; merge/split có audit và có thể hoàn tác.
- Tracked link/form có signed token ngắn gọn, không lộ ID tuần tự.
- Consent record append-only; unsubscribe ưu tiên cao hơn mọi workflow marketing.
- Import/export có quyền, rate limit, audit và chống CSV injection.

### EPIC D — Relationship workspace (P0)

- Contact 360: timeline, source, campaign, last/next action, owner.
- Task queue theo SLA và trạng thái overdue.
- Note/template an toàn; attachment dùng object authorization.
- Inbox adapter theo capability ở phase sau; MVP cho phép ghi nhận tương tác thủ công và form event.

### EPIC E — Offer, order và attribution (P0)

- Offer catalog tối giản cho service/product/subscription/credit.
- CTA/landing/form mapping tới offer và campaign.
- Conversion event idempotent: qualified, accepted, paid, refunded, renewed.
- Last-known attribution công bố rõ; giữ touchpoint để nâng cấp sau.
- Revenue/margin dashboard tách gross revenue, refund, fee và direct variable cost.

### EPIC F — Automation và n8n (P1)

- Outbox events: `contact.captured`, `consent.changed`, `task.overdue`, `offer.sent`, `conversion.paid/refunded`.
- Workflow mẫu: assign owner, SLA reminder, nurture có consent, post-purchase follow-up.
- Idempotency, retry, dead-letter, kill switch và manual replay.
- n8n không giữ source of truth và không nhận secret/PII ngoài allow-list cần thiết.

### EPIC G — Monetization của VidTrans (P0)

- Gói giả thuyết:
  - `Creator`: content calendar + giới hạn phút render/bài đăng/contact active.
  - `Pro`: automation, attribution và số connection cao hơn.
  - `Agency`: nhiều workspace/brand, seat, approval và báo cáo khách hàng.
- Add-on: phút xử lý, active contacts, connection, seat hoặc managed onboarding; không tính tiền theo message nếu chưa đo được cost/policy ổn định.
- Pilot dùng pricing interview + willingness-to-pay; không khóa giá trước khi có cost và conversion evidence.

## 5. API và dữ liệu dự kiến

### API nhóm Content

```text
POST/GET    /api/v1/campaigns
POST/GET    /api/v1/campaigns/{id}/variants
POST        /api/v1/content-variants/{id}/approve
POST        /api/v1/content-variants/{id}/schedule
GET         /api/v1/content-calendar
```

### API nhóm Relationship

```text
POST/GET    /api/v1/contacts
GET         /api/v1/contacts/{id}/timeline
POST        /api/v1/contacts/{id}/tasks
POST        /api/v1/contacts/{id}/consents
POST        /api/v1/contact-merges/{id}/review
```

### API nhóm Commerce

```text
POST/GET    /api/v1/offers
POST        /api/v1/touchpoints/capture
POST        /api/v1/conversions
GET         /api/v1/analytics/content-to-revenue
```

Public capture endpoint chỉ nhận signed campaign token, validate/rate-limit và không cho client tự khai `tenant_id`, owner hoặc revenue.

## 6. Kế hoạch BA → DEV → TEST → DEPLOY

| Bước | Đầu vào | Việc làm | File đầu ra dự kiến | Cách kiểm chứng | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| BA-1 | Plan được duyệt + câu trả lời ICP | Viết URD, UC/BR/AC/NFR, state model và KPI dictionary | `BA_URD/content-relationship-commerce/URD.md` | Review traceability và decision log | ICP/offer/kênh | NOT_STARTED |
| BA-2 | URD | Threat/privacy review, consent, identity merge, retention | `REVIEW.md`, `OPEN_QUESTIONS.md` | Security/privacy review | BA-1 | NOT_STARTED |
| DEV-1 | URD approved | Thiết kế schema/API/outbox, mapping vào publishing/billing hiện có | `DEV/content-relationship-commerce/DESIGN.md` | Design, migration, rollback review | BA approved | NOT_STARTED |
| DEV-2 | Design approved | Làm vertical slice Campaign → Schedule | Source + unit/integration tests | Targeted suite, không gọi provider thật | Existing publish service | NOT_STARTED |
| DEV-3 | Slice 1 ổn định | Làm Contact → Consent → Task | Source + tests | Cross-tenant, merge/split, suppression | Auth/RBAC | NOT_STARTED |
| DEV-4 | Slice 2 ổn định | Làm Offer → Paid → Attribution | Source + tests | Webhook idempotency/reconcile | Billing contract | NOT_STARTED |
| DEV-5 | Core stable | Automation + dashboard | Source/workflows + tests | Duplicate/outage/kill-switch drill | Outbox/n8n | NOT_STARTED |
| TEST-1 | AC + build | Functional, RBAC/IDOR, consent, retry, duplicate, attribution cases | `TEST/.../TESTCASES.md` | AC → TC coverage | Stable test env | NOT_STARTED |
| TEST-2 | Pilot build | E2E content-to-revenue, load, privacy/export/delete and UAT | `TEST_REPORT.md` | Evidence PASS/FAIL/BLOCKED | Pilot synthetic data | NOT_STARTED |
| DEPLOY-1 | RC | Migration, feature flag, backfill, manifest, runbook, rollback | `DEPLOY/.../` | Staging rehearsal | Infra/config | NOT_STARTED |
| DEPLOY-2 | Go-live approval | Canary theo tenant, monitor publish/contact/conversion | Release record | Go/no-go + rollback criteria | Pilot cohort | NOT_STARTED |

## 7. Test gates bắt buộc

- User A không đọc/sửa/export contact, campaign, task, offer hay conversion của tenant B.
- Double-click/retry/restart không tạo publish hoặc conversion trùng.
- Hai contact trùng tên nhưng khác verified identity không bị tự gộp.
- Merge contact có thể undo mà không mất timeline/consent/audit.
- Unsubscribe/revoke consent chặn marketing automation tiếp theo.
- Provider/n8n/payment timeout không làm mất source-of-truth state.
- Paid và refund được reconcile; dashboard không tính submitted/pending là doanh thu thực nhận.
- Feature flag rollback không xóa dữ liệu mới hoặc làm publish attempts đang chạy bị lặp.

## 8. Definition of Done

### Product

- Pilot user hoàn thành luồng asset → multi-platform schedule → captured contact → offer → paid.
- Dashboard trả lời được “nội dung/campaign nào tạo doanh thu” theo attribution model công bố.
- Có bằng chứng giảm thời gian vận hành và ít nhất một cohort sẵn sàng trả phí.

### Engineering

- Tenant/object authorization, consent, idempotency, audit và reconciliation có automated tests.
- Schema additive, migration/rollback rehearsal đạt; không log token, URL nhạy cảm hoặc PII thừa.
- Publish, billing và relationship state tách biệt nhưng liên kết bằng ID/correlation rõ ràng.

### Operations

- Có dashboard SLO, error budget, provider health, publish lag, task SLA và revenue reconciliation.
- Có kill switch cho publishing/automation, runbook incident và quy trình export/delete dữ liệu.

## 9. Rủi ro, giả định và blocker

| Loại | Nội dung | Hướng xử lý |
|---|---|---|
| BLOCKER | Chưa chốt ICP và offer đầu tiên | Chọn một ICP + một offer trước URD |
| BLOCKER | Chưa rõ “1 user” là audience contact hay customer của VidTrans | Xác nhận để khóa domain model |
| OPEN | Kênh chốt đơn/1:1 ưu tiên | Bắt đầu tracked form + task; chỉ thêm adapter sau khi chốt |
| OPEN | Mức automation được phép | Mặc định suggestion/manual approval |
| ASSUMPTION | Lõi publish/billing hiện hữu được tái sử dụng | Kiểm chứng interface trong DESIGN |
| RISK | Scope phình thành CRM tổng quát | Giữ MVP theo content-to-revenue và một ICP |

## 10. Tự review plan

- **Scope:** tập trung đúng content-to-revenue; không xây CRM tổng quát hay omnichannel inbox trong MVP.
- **Compatibility:** tái sử dụng publishing, social connection, tenant/billing và outbox hiện có; thay đổi schema additive.
- **Security/privacy:** tenant isolation, consent, deterministic identity, suppression, audit và PII minimization là gate P0.
- **Testability:** chia ba vertical slice có input/output và chỉ số riêng; provider ngoài được fake/contract test.
- **Rollback:** feature flag theo tenant; không xóa record mới khi rollback; attempt có remote ID chỉ reconcile, không tạo lại.
- **Gap cần review:** ICP, nghĩa chính xác của quan hệ 1:1, offer đầu tiên, kênh chốt đơn và mức tự động hóa.

> Đây là điểm review của quy trình. Chỉ chuyển sang viết URD/thiết kế/coding sau khi người dùng duyệt plan và trả lời các blocker ở mục 9.
