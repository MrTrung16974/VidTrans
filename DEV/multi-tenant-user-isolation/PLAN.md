# Plan: Cô lập dữ liệu và cấu hình theo người dùng

## Trạng thái triển khai

- [x] P0 schema owner cho job/batch và backfill dữ liệu cũ về bootstrap owner.
- [x] P0 scope danh sách, chi tiết, batch, cancel, retry, delete và status theo user.
- [x] P0 khóa tải video/subtitle/JSON/ZIP theo owner; URL artifact mới có `job_id`.
- [x] P0 lọc WebSocket theo owner và xóa state cũ trên frontend khi logout/hết phiên.
- [x] P1 bước đầu: khóa draft và publish attempt theo owner job.
- [x] P1 namespace vật lý file theo user và migration file cũ (copy an toàn, giữ bản legacy để rollback).
- [x] P1 tách OAuth/token tài khoản mạng xã hội theo user trên TikTok, Instagram, Threads, Facebook và YouTube; OAuth state bind user và dùng một lần.
- [x] P1 màn audit riêng cho admin (API /api/v1/admin/jobs, /api/v1/admin/audit-events, /api/v1/admin/users/{id}/quota, giao diện subtabs giám sát jobs và nhật ký kiểm toán an ninh).
- [x] P2 quota theo gói (30/300/1000 phút/tháng), reserve batch nguyên tử, điều chỉnh theo duration thật và consume/release theo kết quả.
- [x] P2 hardening: trigger DB khóa owner/batch chéo tenant, quota identity và policy retention opt-in theo thời gian.
- [x] Isolation matrix cấp API: list/detail/batch/artifact/ZIP và admin view được kiểm tra với User A, User B, Admin.
- [x] Manifest đối soát migration idempotent tại `work/migrations/tenant-storage-v1.json`, có số job/file và danh sách file legacy bị thiếu.

## 1. Mục tiêu

- Mỗi user chỉ được đọc, sửa, hủy, chạy lại, xóa và tải xuống dữ liệu do chính user đó tạo.
- Mọi truy vấn nghiệp vụ phải có `user_id`; thiếu scope user phải bị từ chối mặc định.
- Theo chính sách vận hành mới, owner/admin thấy và quản lý toàn bộ video ngay tại màn Tiến trình; user thường vẫn chỉ thấy dữ liệu của mình. Mỗi dòng luôn hiện user tạo video.
- Dữ liệu cần cô lập gồm batch, job, cấu hình xử lý, file upload/output, archive ZIP, trạng thái realtime, draft/publish attempt và tài khoản mạng xã hội.
- Queue worker vẫn xử lý chung toàn hệ thống nhưng không làm mất ownership.

## 2. Hiện trạng và lỗ hổng

| Thành phần | Hiện trạng | Rủi ro |
|---|---|---|
| `jobs`, `batches` | Không có `user_id` | List/get/mutation trả dữ liệu toàn cục |
| `/api/v1/jobs*`, `/api/v1/batches*`, `/status/*` | Không lọc account hiện tại | Đọc/sửa/xóa chéo user bằng ID |
| `/download/{filename}` và ZIP | Kiểm tra filename/path nhưng không kiểm tra owner | Tải file chéo user nếu biết URL |
| WebSocket `/api/v1/jobs/events` | Xác thực kết nối nhưng broadcast snapshot dùng chung | Mọi user nhận cập nhật job của nhau |
| Queue position | Tính toàn cục và trả kèm job | Có thể làm lộ hoạt động user khác |
| Publish/draft attempts | Chủ yếu gắn `job_id`/`account_id`, chưa có owner xuyên suốt | Publish hoặc xem draft chéo user |
| Billing | Đã có `user_id` | Giữ nguyên, bổ sung test hồi quy |
| Social credential store mới | Đã có `user_id` | Bắt buộc routes/publishers luôn dùng store theo user |
| Source browser/cookie | Dùng chung, admin-only | Giữ là cấu hình hệ thống, không cấp cho user thường |

## 3. Quy tắc phân quyền đích

1. `editor` và `viewer`: chỉ thấy dữ liệu thuộc `current_account.user_id`; viewer tiếp tục chỉ đọc.
2. `admin` và `owner`: xem và thao tác toàn bộ job/batch/video của hệ thống; quota retry vẫn tính cho user sở hữu job.
3. `/api/v1/admin/...` tiếp tục là màn audit/hỗ trợ chuyên sâu, yêu cầu owner/admin và ghi audit event.
4. Truy cập chéo user trả `404` thay vì `403` để không xác nhận ID có tồn tại.
5. Không nhận `user_id` từ form/query của user. Backend lấy duy nhất từ JWT/session.
6. Worker nội bộ được phép lấy job toàn cục từ queue, nhưng không dùng API user-facing.

## 4. Thiết kế dữ liệu và migration

### 4.1 Schema

- Thêm `owner_user_id TEXT` vào `jobs` và `batches`.
- Tạo index:
  - `idx_jobs_owner_created(owner_user_id, created_at DESC)`
  - `idx_jobs_owner_status(owner_user_id, status, updated_at DESC)`
  - `idx_batches_owner_created(owner_user_id, created_at DESC)`
- Thêm `owner_user_id` vào TikTok/social publish attempts và các bảng draft/status liên quan.
- Giữ `account_id` là ID tài khoản nền tảng; không dùng nó thay cho user VidTrans.

### 4.2 Dữ liệu cũ

1. Migration additive: thêm cột nullable.
2. Backfill toàn bộ record cũ về bootstrap owner `admin@local.vidtrans`.
3. Kiểm tra không còn record null/orphan.
4. Chuyển logic ứng dụng sang bắt buộc ownership.
5. Với SQLite không hỗ trợ alter constraint thuận tiện, enforce `NOT NULL` ở store/service và thêm trigger kiểm tra insert/update; rebuild table ở maintenance release sau.
6. Backup database và manifest file trước migration; migration phải idempotent.

## 5. Thay đổi backend

### 5.1 Store/service

- Đổi các hàm user-facing thành chữ ký bắt buộc scope:
  - `create(..., owner_user_id=...)`
  - `create_batch(..., owner_user_id=...)`
  - `get_for_user(job_id, owner_user_id)`
  - `list_jobs(owner_user_id, ...)`
  - `get_batch_for_user(...)`, `list_batches(owner_user_id, ...)`
  - `request_cancel_for_user`, `retry_for_user`, `delete_for_user`
- Không dùng tham số `user_id=None` trong đường user-facing vì dễ vô tình query toàn cục.
- Tách interface worker: `claim_next_system`, `get_system`, `update_system`; chỉ scheduler/pipeline được dùng.
- Batch chỉ được attach job cùng owner; vi phạm phải rollback transaction.

### 5.2 API

- Lấy `account = current_account(request)` tại tất cả endpoint jobs/batches/status/download/publish.
- Scope ownership cho:
  - list/detail jobs và batches;
  - cancel/retry/delete/refresh status;
  - TikTok draft, caption, prepare và publish;
  - social publish create/list/refresh/cancel;
  - download video/subtitle/json/ZIP.
- Thay `/download/{filename}` bằng URL có job context, ví dụ `/api/v1/jobs/{job_id}/artifacts/{kind}`. Filename không còn là capability truy cập.
- URL cũ chỉ redirect sau khi kiểm tra owner trong thời gian tương thích; sau đó loại bỏ.
- `job_response` không trả đường dẫn filesystem, cookie path, resume payload hoặc ID nền tảng nhạy cảm.

### 5.3 File storage

- Chuyển layout sang namespace:
  - `uploads/{user_id}/{batch_id}/{job_id}/...`
  - `outputs/{user_id}/{job_id}/...`
  - `work/{user_id}/{job_id}/...`
- Luôn resolve path và xác minh path nằm trong root của user hiện tại.
- Archive ZIP tạo trong thư mục tạm riêng, kiểm tra mọi artifact cùng owner trước khi đóng gói.
- Migration file cũ thực hiện move/copy atomic; record chỉ đổi path sau khi file đích hợp lệ.

### 5.4 Realtime

- WebSocket subscriber lưu `user_id` và role, không chỉ trạng thái authenticated.
- Event hub không broadcast một snapshot chung. Có hai lựa chọn:
  1. publish `job_id` rồi build snapshot theo từng `user_id`; hoặc
  2. event mang `owner_user_id`, chỉ enqueue cho subscriber cùng owner.
- Chọn phương án 2 để giảm query và tránh dữ liệu chéo xuất hiện trong memory queue của client khác.
- Sự kiện wildcard/resync chỉ yêu cầu từng client reload danh sách của chính user.
- Queue position user-facing chỉ trả vị trí của job đó; không trả danh sách/ID toàn cục.

### 5.5 Cấu hình và tài khoản mạng xã hội

- Cấu hình batch/preset lưu theo user; nếu bổ sung preset table dùng unique `(user_id, preset_name)`.
- OAuth state, token và connection phải truy xuất bằng cả `user_id` + `connection_id`.
- Publisher không được đọc token global theo `account_id` do client gửi lên.
- Source-browser session/cookies tiếp tục là system config admin-only; user thường chỉ hưởng kết quả downloader, không xem/sửa credential.

## 6. Plan/quota theo gói

- Tạo usage ledger theo `(user_id, billing_period)` gồm reserved/consumed minutes.
- Khi tạo batch: kiểm tra plan và reserve quota trong cùng transaction; reject `402/409` nếu vượt quota.
- Khi job hoàn tất: chuyển reserved sang consumed theo duration thực tế; failed/cancelled thì release.
- Không suy ra quota từ số job đang hiển thị.
- Admin điều chỉnh plan/quota phải có audit event.

## 7. Frontend

- Không coi filter frontend là biện pháp bảo mật; frontend chỉ render dữ liệu API đã scope.
- Sau login/logout phải clear `state.jobs`, `selectedJobIds`, pending upload, draft social và cache account trước khi tải user mới.
- WebSocket reconnect sau khi đổi phiên; đóng socket cũ trước khi logout.
- Hiển thị empty state riêng cho user mới thay vì dữ liệu mẫu/toàn cục.
- Admin cần xem dữ liệu user khác phải vào màn Admin riêng và chọn user rõ ràng.

## 8. Kiểm thử bắt buộc

### P0 isolation matrix

Tạo User A, User B và Admin; với mỗi tài nguyên của A kiểm tra B không thể:

- list/search/filter;
- get detail/status/batch config;
- cancel/retry/delete;
- download video/subtitle/ZIP;
- xem sự kiện WebSocket;
- mở draft/caption/publish attempt;
- dùng social connection/account ID của A.

Kết quả mong đợi: `404`, không thay đổi dữ liệu/file, không phát event cho B.

### Migration và worker

- Record legacy được gán owner đúng và không mất file.
- Scheduler vẫn claim job của mọi user theo queue toàn cục.
- Update từ worker giữ nguyên `owner_user_id`.
- Xóa user xử lý theo policy retention, không xóa nhầm artifact user khác.
- Test race: logout/login user khác trên cùng browser không giữ job/cache/socket cũ.

## 9. Thứ tự triển khai

1. **P0 - Chặn lộ dữ liệu:** schema owner + backfill + scope list/detail/mutation/download + test cross-user.
2. **P0 - Realtime:** WebSocket/event hub theo owner; clear frontend state khi đổi phiên.
3. **P1 - Artifact namespace:** URL download theo job và migration file.
4. **P1 - Publishing:** ownership cho draft/attempt/social account trên mọi nền tảng.
5. **P1 - Admin audit view:** API/màn riêng để support có kiểm soát.
6. **P2 - Quota:** usage ledger và enforcement theo plan.
7. **P2 - Hardening:** DB constraints, audit log, retention/delete-account, benchmark index.

## 10. Điều kiện hoàn tất

- Không endpoint user-facing nào gọi store toàn cục mà thiếu `owner_user_id`.
- Bộ test isolation A/B phủ toàn bộ API và download, tất cả PASS.
- WebSocket của B không nhận payload chứa job ID/filename của A.
- Filename hoặc job ID bị lộ không đủ để tải artifact.
- Dữ liệu legacy được migrate có báo cáo đối soát số record và file.
- Owner/admin chỉ xem toàn hệ thống qua endpoint Admin có audit.
