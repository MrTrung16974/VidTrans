# URD — Đăng TikTok trực tiếp và đặt lịch

- Feature: `tiktok-direct-scheduled-upload`
- Version: `0.1`
- Trạng thái: `APPROVED_SCOPE`
- Approval record: người dùng xác nhận “oki bắt đầu” ngày `2026-10-10` cho PLAN 0.1.

## 1. Tổng quan

VidTrans cho phép quản trị viên xem video đã render, duyệt caption/hashtag và chọn một trong ba hành động qua TikTok Content Posting API chính thức: đăng ngay, đặt lịch nội bộ hoặc gửi bản nháp. Native schedule qua API web private không thuộc release 0.1.

## 2. Actor và tiền điều kiện

- Actor: quản trị viên đã đăng nhập VidTrans.
- Video thuộc job `completed`, artifact còn tồn tại trong output directory.
- TikTok Developer App được cấu hình và account đã cấp scope phù hợp.

## 3. Use cases

### UC_01 — Đăng ngay

1. Người dùng chọn video, sửa caption và privacy.
2. Người dùng xác nhận đã duyệt rồi bấm `Đăng ngay`.
3. Hệ thống tạo publish attempt idempotent, upload và submit qua Direct Post.
4. Hệ thống hiển thị riêng trạng thái đã nhận yêu cầu, đang xử lý và đã đăng.

### UC_02 — Đặt lịch

1. Người dùng chọn ngày giờ tương lai theo timezone trình duyệt.
2. Hệ thống lưu UTC và giữ attempt qua restart.
3. Đến hạn, một worker duy nhất claim attempt và thực hiện UC_01.
4. Người dùng có thể hủy trước khi attempt được claim.

### UC_03 — Gửi bản nháp

1. Người dùng duyệt video rồi chọn `Gửi bản nháp`.
2. Hệ thống upload qua inbox API.
3. UI nói rõ người dùng phải hoàn tất trong ứng dụng TikTok.

### UC_04 — Theo dõi và phục hồi

1. Hệ thống lưu remote `publish_id` ngay sau init.
2. Nếu upload/submit lỗi sau khi có remote ID, attempt chuyển `NEEDS_RECONCILIATION`, không tự đăng lại.
3. Nếu chưa có remote ID, attempt có thể thất bại an toàn và người dùng tạo yêu cầu mới.

## 4. Business rules

- `BR01_01`: Caption không rỗng cho Direct Post, tối đa 2.200 UTF-16 code units.
- `BR01_02`: Caption snapshot phải giữ nguyên newline, hashtag và mention đã duyệt.
- `BR01_03`: Privacy phải thuộc capability trả về từ creator info.
- `BR01_04`: Mỗi request bắt buộc có idempotency key; cùng key trả lại cùng attempt.
- `BR02_01`: Lịch phải có timezone và ít nhất 30 giây trong tương lai, tối đa 365 ngày.
- `BR02_02`: Lịch được lưu UTC và claim nguyên tử.
- `BR02_03`: Release 0.1 đăng ngay khi worker hoạt động lại, đồng thời lưu độ trễ lịch.
- `BR02_04`: Chỉ hủy được attempt `SCHEDULED_LOCAL` hoặc `READY`.
- `BR03_01`: Draft cần scope `video.upload`; Direct Post cần `video.publish`.
- `BR04_01`: Có remote ID thì không tự upload lại sau lỗi mơ hồ.
- `BR04_02`: Trạng thái processing job không được dùng thay trạng thái publish attempt.

## 5. Acceptance criteria

- `AC01_01`: Payload Direct Post chứa chính xác caption trong UI, kể cả xuống dòng.
- `AC01_02`: Double click/retry cùng idempotency key chỉ tạo một attempt.
- `AC01_03`: UI không gọi `SUBMITTED/PROCESSING_UPLOAD` là đã đăng.
- `AC02_01`: Lịch còn tồn tại và được thực thi sau restart.
- `AC02_02`: Hai worker không claim cùng một attempt.
- `AC02_03`: Hủy lịch trước claim không gọi TikTok.
- `AC03_01`: Draft hiển thị hướng dẫn hoàn tất trên TikTok.
- `AC04_01`: Timeout sau init có remote ID chuyển reconciliation, không retry tạo bài.
- `AC04_02`: API response/log không lộ token, cookie hay upload URL.

## 6. NFR

- `NFR_01`: SQLite transaction dùng parameter binding và `BEGIN IMMEDIATE` khi claim/update cạnh tranh.
- `NFR_02`: Scheduler poll tối đa mỗi giây và dừng sạch khi ứng dụng shutdown.
- `NFR_03`: Secret chỉ do publisher/credential store quản lý, không nằm trong publish payload.
- `NFR_04`: Unit/integration test không gọi TikTok thật.
- `NFR_05`: Schema additive; rollback code không xóa publish attempts đã có remote ID.

## 7. Ngoài phạm vi 0.1

- Native schedule qua session cookie/private endpoint.
- Tự vượt CAPTCHA/OTP.
- Sửa/hủy bài đã submit lên TikTok.
- Nhiều TikTok account trong UI.
