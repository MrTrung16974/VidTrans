# ANALYSIS — VidTrans multi-tenant và thương mại hóa

- Feature: `multi-tenant-commercial-platform`
- Phiên bản: `0.2-draft`
- Ngày phân tích: `2026-10-10`
- Trạng thái: `REVIEWED — plan 0.2 đã duyệt`

## 1. Vấn đề và mục tiêu

VidTrans hiện là modular monolith có pipeline upload/link → download → OCR/ASR → dịch → TTS/render → artifact; có hàng đợi SQLite, đăng TikTok và xác thực quản trị đơn. Để bán như SaaS cần tách dữ liệu, quota, credential và tài nguyên xử lý theo người dùng/tổ chức; đồng thời có trang quản trị vận hành và cơ chế tính phí.

Mục tiêu MVP thương mại:

- Nhiều người dùng đăng ký/đăng nhập, mỗi người chỉ thấy dữ liệu và kết nối mạng xã hội của mình.
- Admin có toàn quyền quản trị người dùng, gói, quota, job, chi phí, sự cố và audit.
- User được upload video để dịch; tải nguồn từ Douyin/TikTok, sau đó Facebook/YouTube; đăng kết quả lên TikTok bằng tài khoản đã tự kết nối.
- Có quota/credit, thanh toán, giới hạn tài nguyên và số liệu unit economics đủ để bán thử.
- n8n hỗ trợ orchestration ngoại vi nhưng không nằm trên data path của file video lớn.

Chỉ số đề xuất cho pilot:

- Tỷ lệ job hoàn thành >= 95% (không tính lỗi URL/quyền truy cập do nguồn).
- p95 thời gian chờ bắt đầu job < 5 phút ở tải thiết kế của pilot.
- 100% API đọc/ghi tài nguyên có kiểm tra tenant/object ownership.
- 100% thao tác admin nhạy cảm và thay đổi quota có audit log.
- Có số liệu chi phí/job và gross margin theo gói trước khi mở bán công khai.

## 2. AS-IS (CONFIRMED từ repository)

- FastAPI + frontend tĩnh; kiến trúc modular monolith.
- SQLite lưu batch/job và trạng thái đăng TikTok; worker nội bộ có giới hạn concurrency.
- Auth hiện là một tài khoản admin cấu hình từ môi trường, JWT HS256; chưa có user database/RBAC/multi-tenant.
- Downloader dùng `yt-dlp`, hiện allow-list TikTok/Douyin, giới hạn dung lượng/thời lượng và có cơ chế cookie/browser cho Douyin.
- Pipeline dịch, OCR/ASR, TTS, render và artifact đã có; TikTok publishing dùng API chính thức và có idempotency/reconcile.
- File nguồn/kết quả lưu filesystem cục bộ; chưa có object storage, retention theo gói hoặc metering/billing.

## 3. TO-BE

### Actor và quyền

| Actor | Quyền chính | Giới hạn bắt buộc |
|---|---|---|
| `ADMIN` | Full quyền quản trị hệ thống, user, plan, quota, job, provider, audit | MFA; audit; không xem token/secret thô; thao tác phá hủy cần xác nhận |
| `USER` | Upload và dịch file; download từ nguồn được hỗ trợ; tải artifact; kết nối và đăng TikTok; xem usage/billing của mình | Chỉ tài nguyên thuộc tenant; theo quota/gói; phải xác nhận quyền sử dụng nội dung |
| `WORKER/SERVICE` | Claim và chạy job theo service identity | Không có quyền user/admin; scope tối thiểu; idempotent |

Mô hình dữ liệu đề xuất: `organizations/tenants`, `users`, `memberships`, `roles`, `plans`, `subscriptions`, `usage_ledger`, `batches`, `jobs`, `artifacts`, `social_connections`, `publish_attempts`, `audit_logs`, `webhook_events`. MVP có thể dùng tenant cá nhân 1 thành viên nhưng giữ schema sẵn cho team.

### Luồng nghiệp vụ mục tiêu

1. User đăng nhập → chọn upload hoặc URL nguồn → hệ thống preflight platform/size/duration/quota.
2. API tạo job gắn `tenant_id`, ghi usage reservation và đẩy queue.
3. Downloader adapter lấy file về object storage/quarantine, kiểm tra media và quyền truy cập.
4. Media worker chạy pipeline; cập nhật stage/progress; artifact lưu bằng key có tenant prefix.
5. Usage ledger chốt số phút/compute/storage; hoàn credit hợp lệ khi job lỗi hệ thống.
6. User tải artifact qua signed URL ngắn hạn hoặc chọn đăng TikTok.
7. Publisher dùng OAuth connection của đúng user/tenant, idempotency key và reconcile trạng thái.
8. Admin quan sát job/chi phí/lỗi và có hành động retry/cancel/suspend theo policy, mọi hành động được audit.

## 4. Phạm vi theo giai đoạn

### MVP thương mại

- Multi-tenant auth, RBAC `ADMIN`/`USER`, ownership middleware/policy.
- User portal: upload → dịch → tiến trình → download artifact.
- Download TikTok/Douyin trên nền adapter hiện có; thêm Facebook/YouTube sau contract test.
- Kết nối OAuth và đăng TikTok theo từng user; consent rõ trước khi đăng.
- Admin page: dashboard, users, plans/quota, jobs, provider health, usage, audit.
- PostgreSQL, Redis-backed job queue, S3-compatible object storage, signed URL, retention.
- Credit/metering và một cổng thanh toán phù hợp thị trường; invoice/webhook idempotent.
- Observability, backup/restore, rate limit, abuse controls, legal consent/takedown.

### Sau MVP

- Team/workspace nhiều thành viên và role chi tiết hơn.
- Upload YouTube/Facebook và lịch nội dung đa nền tảng.
- Auto-scaling GPU workers, multi-region, affiliate/reseller, white-label.
- Marketplace workflow hoặc cho khách tự chỉnh n8n.

### Ngoài phạm vi hiện tại

- Bypass DRM/paywall/private content hoặc cơ chế chống bot của nền tảng.
- Cam kết downloader hoạt động vĩnh viễn cho website bên thứ ba.
- Đăng công khai TikTok trước khi app/API scope được platform audit/approve.
- Cho user truy cập trực tiếp n8n editor/credential store trong MVP.

## 5. Nghiên cứu n8n và quyết định đề xuất

### Nên dùng n8n cho

- Nhận webhook thanh toán, CRM, email/Telegram/Slack thông báo.
- Workflow hậu xử lý: job hoàn tất → tạo notification → cập nhật CRM/analytics.
- Back-office có human approval: duyệt tài khoản rủi ro, hoàn credit, cảnh báo provider lỗi.
- Prototype connector mới trước khi viết adapter production trong backend.

### Không nên dùng n8n cho

- Truyền file video lớn qua từng node, chạy ffmpeg/OCR/ASR/TTS hoặc làm source of truth của job.
- Quyết định phân quyền, quota, billing ledger hoặc giữ OAuth token của user nếu backend đã có vault/token service.
- Thay job queue media: pipeline cần checkpoint, cancellation, resource class và retry/idempotency chuyên biệt.

### Cách tích hợp

`VidTrans API/Outbox → signed webhook nhỏ → n8n → SaaS ngoài`; chiều ngược lại dùng service account scope hẹp, HMAC/mTLS, idempotency key và allow-list endpoint. Payload chỉ mang ID/metadata, không mang binary; n8n gọi API để lấy dữ liệu tối thiểu cần thiết.

n8n queue mode dùng Redis và worker, nhưng tài liệu chính thức không khuyến nghị SQLite; binary trong queue mode cũng không dùng filesystem. S3 external storage của n8n là tính năng trả phí Business/Enterprise. Vì vậy MVP nên self-host n8n đơn/queue nhỏ cho automation metadata, còn video ở object storage của VidTrans. Cần kiểm tra giấy phép/OEM riêng nếu sau này nhúng hoặc bán quyền truy cập n8n như một phần sản phẩm.

## 6. Rủi ro và kiểm soát

| Rủi ro | Mức | Kiểm soát đề xuất |
|---|---:|---|
| IDOR/rò dữ liệu giữa user | Critical | Tenant ID từ security context; object-level authorization; test cross-tenant; deny by default |
| OAuth/social token bị lộ | Critical | Encrypt envelope/KMS, scope tối thiểu, rotation/revoke, không log/không trả token |
| Nội dung vi phạm bản quyền/ToS | High | Chỉ nội dung user sở hữu/được phép; checkbox + policy; takedown; không DRM/private; rate/abuse limit |
| Extractor thay đổi/bị chặn | High | Adapter từng platform, contract/smoke test, circuit breaker, status page; không SLA cứng theo từng nguồn |
| Chi phí compute vượt doanh thu | High | Preflight, reservation credit, hard quota, worker pool theo resource, cost ledger và kill switch |
| File độc hại/SSRF | High | Allow-list host, DNS/IP validation sau redirect, limit redirect/size/time, quarantine, media probe, egress policy |
| Retry gây đăng trùng/tính tiền trùng | High | Idempotency key, outbox/inbox, state machine, reconcile trước retry |
| SQLite/filesystem không chịu multi-instance | High | Chuyển PostgreSQL + object storage trước public beta; queue broker tách khỏi DB |
| TikTok app chưa audit | High | Pilot private/draft; hoàn tất audit và UX consent trước public launch |

## 7. Nguồn bằng chứng

- Repository: `README.md`, `ARCHITECTURE.md`, module auth/job/downloader/TikTok publishing (đọc ngày 2026-10-10).
- n8n queue mode và binary: https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/enable-queue-mode
- n8n external storage/licensing: https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/use-external-storage
- TikTok Direct Post/audit: https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post
- YouTube resumable upload: https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
- yt-dlp supported sites (có Douyin, Facebook, TikTok, YouTube nhưng không bảo đảm luôn hoạt động): https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md

## 8. Giả định và câu hỏi mở

### ASSUMPTION

- Sản phẩm SaaS phục vụ creator/agency nhỏ tại Việt Nam trước.
- MVP ưu tiên web app và một khu vực triển khai; chưa cần enterprise SSO.
- Người dùng tự kết nối tài khoản TikTok của họ; admin không chia sẻ một token dùng chung.

### CONFIRMED — quyết định ngày 2026-10-10

- Subscription giới hạn theo tổng số phút video; có thể mua thêm credit phút.
- Thanh toán ưu tiên thị trường Việt Nam.
- File nguồn và kết quả được lưu 60 ngày, sau đó tự động xóa theo retention policy.
- Admin có full quyền, bao gồm xem và tải video người dùng. Việc truy cập nội dung phải có audit log, lý do hỗ trợ và hạn chế tải hàng loạt để giảm rủi ro lạm dụng.
- Ngân sách/hạ tầng pilot sẽ tính sau; chưa đủ dữ liệu để chốt sizing hoặc nhà cung cấp.
- MVP phục vụ tài khoản cá nhân; schema vẫn giữ `tenant/membership` để nâng cấp agency mà không phải đổi mô hình dữ liệu.

### OPEN — cần chốt trước URD/coding

1. Phút được tính theo video nguồn, video đầu ra hay phút xử lý thực tế khi retry? Đề xuất: phút video nguồn cho mỗi yêu cầu thành công; retry do lỗi hệ thống không trừ thêm.
2. Mức phút/tháng và giá của từng gói là bao nhiêu?
3. Cho phép tải YouTube/Facebook ngay MVP hay để beta sau khi legal/contract test?
4. Chọn cổng thanh toán Việt Nam cụ thể sau khi so sánh phí, đối soát, webhook và điều kiện pháp nhân.
5. Hạ tầng mục tiêu và ngân sách tháng: một VPS, cloud CPU/GPU, hay hybrid?

