# PLAN — VidTrans multi-tenant, Admin và thương mại hóa

- Feature: `multi-tenant-commercial-platform`
- Version: `0.2`
- Nguồn yêu cầu: yêu cầu người dùng ngày `2026-10-10` + kiểm kê repository + tài liệu chính thức
- Trạng thái: `APPROVED`
- Approval record: `Version 0.2 | Người dùng | 2026-10-10 | Duyệt plan và MVP tài khoản cá nhân; giữ tenant/membership để mở rộng agency`

## 1. Kết quả mong muốn

Ra mắt SaaS pilot có multi-tenant an toàn, trang Admin, user upload/download/dịch/đăng TikTok, quota và thanh toán; có thể mở rộng downloader Facebook/YouTube và dùng n8n cho automation ngoại vi mà không làm pipeline media kém ổn định.

## 2. Kiến trúc đích

```text
Web User / Admin
       |
 API Gateway + FastAPI (Auth, RBAC, Tenant, Billing, Job API)
       |             |                 |
 PostgreSQL       Redis Queue       Outbox/Webhook ----> n8n ----> Email/CRM/Alerts
                       |
          +------------+-------------+
          |                          |
   Download workers             Media workers
 (platform adapters)       (OCR/ASR/translate/TTS/render)
          |                          |
          +------ S3-compatible object storage --------+
                                      |
                       TikTok publisher workers/API
```

Nguyên tắc: control plane (user, quyền, plan, quota, trạng thái) ở FastAPI/PostgreSQL; data plane video ở object storage + worker; n8n chỉ điều phối metadata và tích hợp SaaS.

## 3. Roadmap đề xuất

| Phase | Thời lượng ước tính | Phạm vi | Exit criteria |
|---|---:|---|---|
| 0. Product/legal spike | 1–2 tuần | Chốt ICP, pricing hypothesis, quyền nội dung, TikTok audit path, test Facebook/YouTube/Douyin | Quyết định scope MVP, cost model, risk register được duyệt |
| 1. Platform foundation | 3–4 tuần | PostgreSQL migration, tenant/user/membership, RBAC, object storage, signed URL, Redis queue, audit | Cross-tenant tests pass; backup/restore drill; không còn file/job global trong API mới |
| 2. User + Admin MVP | 3–4 tuần | User portal; Admin dashboard/users/jobs/plans/quota/audit; usage reservation/ledger | Admin và user E2E pass; quota chặn đúng; thao tác admin được audit |
| 3. Media/download hardening | 2–3 tuần | Adapter TikTok/Douyin; Facebook/YouTube beta; SSRF/limit; retention; provider health | Contract tests + failure matrix; không bypass private/DRM; cost/job đo được |
| 4. Social publish + n8n | 2–3 tuần | TikTok OAuth per user, direct/draft, reconcile; outbox; n8n notifications/CRM/payment webhook | Không đăng trùng; revoke token; n8n outage không làm mất job/publish state |
| 5. Billing + pilot | 2–3 tuần | Subscription/credit, webhook idempotent, invoices/usage, observability, support/runbook | 10–20 pilot users; payment/refund tests; SLO dashboard; incident/rollback drill |
| 6. Public beta | 2–4 tuần | Security/performance review, TikTok approval, onboarding, pricing page, ToS/privacy/takedown | Go-live checklist ký duyệt; gross margin mục tiêu đạt; support process hoạt động |

Ước tính tổng: 13–21 tuần cho nhóm nhỏ 2–4 người. Một developer nên thu gọn MVP còn TikTok/Douyin download + TikTok publish và dự kiến 4–6 tháng.

## 4. Backlog theo epic

### EPIC A — Identity, tenancy và RBAC (P0)

- User registration/invite, email verification, reset password, session/revocation; admin MFA.
- `tenant_id` bắt buộc ở resource; policy `ADMIN`/`USER`; server lấy identity từ token đã xác minh.
- Test user A không đọc/sửa/xóa/download/job/social connection của user B.
- Tách legacy single-admin auth qua migration/feature flag, có rollback.

### EPIC B — Storage, queue và processing (P0)

- Chuyển job/batch/publish từ SQLite sang PostgreSQL bằng repository interface.
- Object storage key theo tenant/job; signed upload/download; retention/lifecycle.
- Queue có class `download`, `cpu`, `gpu`, `publish`; checkpoint, cancellation, retry policy, dead-letter.
- Usage reservation trước khi queue; settle/refund khi terminal state.

### EPIC C — User application (P0)

- Dashboard job/batch, upload multipart/resumable, nhập URL, chọn mode/ngôn ngữ.
- Progress từng stage, lỗi hành động được, retry/cancel, download artifact.
- Trang connections và TikTok publish; lịch sử publish/status/reconcile.
- Usage/quota/plan/billing history, cảnh báo gần hết quota.
- Hiển thị số phút đã dùng, đã giữ chỗ, còn lại và ngày reset quota.

### EPIC D — Admin page (P0)

- Overview: active users, queue depth, success rate, compute minutes, storage, revenue/estimated margin.
- Users/tenants: search, status, plan/quota, suspend/reactivate, reset/revoke session.
- Jobs: filter, timeline, safe retry/cancel, xem/tải file user theo full quyền Admin; mọi lần truy cập nội dung phải ghi audit và lý do.
- Plans/billing: plan catalog, credit adjustment có reason + maker/checker cho giá trị lớn.
- Providers/system: health, error rate, circuit breaker/kill switch, worker capacity.
- Audit log: actor, action, target, before/after đã masking, correlation ID.

### EPIC E — Multi-platform download (P0/P1)

- Interface `SourceDownloader` và adapter `TikTok`, `Douyin`, `YouTube`, `Facebook`.
- Allow-list hostname/canonical URL; chống SSRF/redirect; cấm playlist ở MVP; size/duration/time limit.
- Chỉ nội dung public và user xác nhận có quyền; không DRM/paywall/private-account bypass.
- Provider-specific error taxonomy, smoke/contract test định kỳ và degradation UI.
- TikTok/Douyin P0; YouTube/Facebook P1 beta sau legal review và đo độ ổn định.

### EPIC F — TikTok publishing (P0)

- OAuth token per user/tenant, encrypted at rest, scope `video.publish`, revoke/refresh.
- Creator info và UX consent đúng yêu cầu; direct post hoặc inbox draft.
- Idempotency, scheduled state, remote status reconciliation, daily cap awareness.
- Pilot private/draft cho app chưa audit; không hứa public post trước approval.

### EPIC G — n8n automation (P1)

- Dựng n8n private network; không public editor; secrets riêng; backup/version workflow.
- Outbox events: `job.completed/failed`, `subscription.changed`, `quota.low`, `provider.degraded`.
- Workflow mẫu: notification, CRM onboarding, payment reconciliation alert, daily ops digest.
- HMAC/mTLS, replay protection, service scopes; payload metadata, không binary/PII thừa.
- Chaos test: n8n down/delay/duplicate không ảnh hưởng trạng thái job và billing ledger.

### EPIC H — Commercialization và operations (P0)

- Pricing: subscription giới hạn theo số phút video mỗi kỳ; cho phép mua thêm credit phút. Gói giả thuyết: `Free Trial`, `Creator`, `Pro/Agency`.
- Metering đề xuất: giữ chỗ số phút khi tạo job, quyết toán theo thời lượng video nguồn khi job thành công; lỗi hệ thống/retry kỹ thuật không trừ thêm; user chủ động chạy lại thành job mới thì tính lại.
- Thanh toán ưu tiên Việt Nam; chọn cổng sau khi đánh giá QR/chuyển khoản/thẻ nội địa, webhook, hoàn tiền, đối soát và điều kiện pháp nhân.
- Retention thống nhất 60 ngày cho file nguồn và kết quả; cảnh báo trước khi hết hạn, lifecycle tự xóa và audit việc xóa.
- Cost ledger: ASR/OCR/translation/TTS/CPU/GPU, download bandwidth, storage, egress, payment fee, support.
- Guardrail: giá bán mục tiêu >= 2.5–3 lần direct variable cost; hard cap để tránh lỗ ngoài dự kiến.
- Funnel: signup → first upload → first successful export → connect TikTok → first publish → paid.
- Legal: ToS, privacy, DPA/vendor list, copyright/takedown, data deletion/export, content consent.
- Support: status page, incident severity, runbook, refund/credit policy, provider limitation wording.

## 5. Kế hoạch hồ sơ BA → DEV → TEST → DEPLOY

| Bước | Đầu vào | Việc làm | File đầu ra dự kiến | Cách kiểm chứng | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| BA-1 | ANALYSIS + quyết định sản phẩm | Viết URD, use case, BR/AC/NFR và state model | `BA_URD/.../URD.md` | Review traceability, quyền, billing, lỗi | 7 câu hỏi mở | NOT_STARTED |
| BA-2 | URD | Threat model, data classification, retention, tenant boundary | `REVIEW.md`, `OPEN_QUESTIONS.md` | Security review | BA-1 | NOT_STARTED |
| DEV-1 | URD approved | Thiết kế schema/API/migration/queue/storage | `DEV/.../DESIGN.md` | Design review + rollback review | BA approved | NOT_STARTED |
| DEV-2 | Design approved | Triển khai foundation theo vertical slices | source + unit/integration tests | Test cô lập, migration rehearsal | Infra dev/test | NOT_STARTED |
| DEV-3 | Foundation | User/Admin UI, download adapters, publish, billing, n8n outbox | source + workflows | E2E theo tenant | Provider sandbox | NOT_STARTED |
| TEST-1 | AC + build | Testcase functional, RBAC/IDOR, billing, retries, platform matrix | `TEST/.../TESTCASES.md` | Trace AC → TC | Stable test env | NOT_STARTED |
| TEST-2 | Testcases | Load/soak, backup restore, chaos n8n/provider, UAT pilot | `TEST_REPORT.md` | Bằng chứng PASS/FAIL/BLOCKED | Pilot data tổng hợp | NOT_STARTED |
| DEPLOY-1 | Release candidate | Migration, manifest, rollback, monitoring, runbook | `DEPLOY/.../` | Staging rehearsal | Infra + approval | NOT_STARTED |
| DEPLOY-2 | Go-live approval | Canary/limited pilot, theo dõi SLO/cost | release record | Go/no-go checklist | Legal/platform approval | NOT_STARTED |

## 6. Definition of Done

### BA

- Scope, actor, quyền, pricing/metering, retention và platform policy có quyết định rõ.
- Mọi UC có AC; NFR có số đo; traceability không có gap P0.

### DEV

- Không endpoint nào tin `tenant_id/user_id/role` do client gửi để cấp quyền.
- Migration/rollback thử trên dữ liệu tổng hợp; secrets không nằm trong code/log.
- Các side effect billing/publish/webhook idempotent và có reconcile.

### TEST

- Unit/integration/E2E/RBAC/IDOR/SSRF/rate-limit/retry/duplicate webhook được chạy có bằng chứng.
- Load test ở tải pilot; restore backup và n8n/provider outage drill đạt tiêu chí.

### DEPLOY/PILOT

- Dashboard SLO, alert, cost/job, runbook và on-call owner có sẵn.
- Legal pages và consent hoạt động; TikTok trạng thái audit được phản ánh đúng trong sản phẩm.
- Pilot chứng minh activation, conversion và gross margin trước public beta.

## 7. Các quyết định còn mở

1. Chốt cách tính phút: đề xuất theo thời lượng video nguồn của job thành công, không trừ thêm khi retry do lỗi hệ thống.
2. Chốt số phút và giá cho từng gói; chọn cổng thanh toán Việt Nam sau bước đánh giá nhà cung cấp.
3. Chọn hạ tầng/ngân sách pilot sau khi có benchmark thời gian và chi phí cho một phút video.
4. Chốt Facebook/YouTube thuộc MVP hay beta sau và kiểm soát vận hành khi Admin xem/tải nội dung.

## 8. Decision record

| ID | Quyết định | Trạng thái | Nguồn |
|---|---|---|---|
| DR-01 | Subscription giới hạn theo số phút và cho mua thêm credit phút | CONFIRMED | Người dùng, 2026-10-10 |
| DR-02 | Thanh toán ưu tiên Việt Nam | CONFIRMED | Người dùng, 2026-10-10 |
| DR-03 | Lưu file nguồn và kết quả trong 60 ngày | CONFIRMED | Người dùng, 2026-10-10 |
| DR-04 | Admin có full quyền, bao gồm xem/tải video user | CONFIRMED | Người dùng, 2026-10-10 |
| DR-05 | Ngân sách/hạ tầng pilot tính sau | DEFERRED | Người dùng, 2026-10-10 |
| DR-06 | MVP ưu tiên tài khoản cá nhân; giữ schema tenant/membership để mở rộng agency | CONFIRMED | Người dùng duyệt plan, 2026-10-10 |
| DR-07 | n8n chỉ làm automation/control integration, không xử lý binary/media pipeline | CONFIRMED | Người dùng duyệt plan, 2026-10-10 |

## 9. Tự review plan

- Scope/compatibility: giữ modular monolith, trích hạ tầng theo interface; tránh rewrite toàn bộ.
- Security: đã đưa tenant isolation, object authorization, token encryption, SSRF, audit và content policy vào P0.
- Testability: có contract adapter, cross-tenant, idempotency, chaos và migration rehearsal.
- Rollback: yêu cầu dual-read/feature flag hoặc migration có backup/restore; chi tiết sẽ ở DESIGN/RUNBOOK.
- Gap cần review: công thức metering, pricing, payment provider, infra budget, legal stance cho download và kiểm soát vận hành đối với Admin content access.

> Plan `0.2` đã được người dùng duyệt ngày 2026-10-10. Bước kế tiếp là viết URD chi tiết; chưa coding trước khi hoàn tất và review URD/thiết kế.
