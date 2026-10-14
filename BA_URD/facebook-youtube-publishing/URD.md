# URD — Đăng Facebook và YouTube

- Version: `0.1`; trạng thái: `DRAFT`; ngày: `2026-10-14`
- Phạm vi đã duyệt: Facebook Page Reels; YouTube upload video/Shorts; direct và lịch nội bộ.

## UC_01 — Kết nối tài khoản

Editor/Admin bắt đầu OAuth, callback phải khớp state một lần còn hạn. Server khám phá Page/channel,
lưu token ngoài publish payload và cho phép ngắt từng tài khoản.

- `BR01_01`: Facebook chỉ công bố Page mà user được API trả về.
- `BR01_02`: YouTube dùng scope upload tối thiểu và refresh token cho tác vụ nền.
- `AC01_01`: state sai/hết hạn bị từ chối và không lưu token.
- `AC01_02`: API status không trả access/refresh token.

## UC_02 — Đăng ngay

Người dùng chọn video hoàn tất, account, metadata và xác nhận duyệt. Hệ thống tạo attempt
idempotent, upload, lưu remote ID rồi reconcile kết quả.

- `BR02_01`: Facebook đích là Page Reel; YouTube bắt buộc title, privacy mặc định `private`.
- `BR02_02`: có remote ID thì không tự tạo upload/bài mới khi kết quả không chắc chắn.
- `BR02_03`: account ID phải resolve được trong token store phía server.
- `AC02_01`: double click cùng idempotency key chỉ tạo một attempt.
- `AC02_02`: trạng thái processing không được hiển thị là published.
- `AC02_03`: lỗi nền tảng không ảnh hưởng file video hoặc attempt nền tảng khác.

## UC_03 — Đặt lịch và hủy lịch

Scheduler chung claim atomically attempt đến hạn. Attempt `SCHEDULED_LOCAL` được hủy trước
khi claim; sau claim phải theo quy trình reconcile.

- `AC03_01`: restart không làm mất lịch hoặc đăng trùng.
- `AC03_02`: hủy lịch thành công không gọi provider.

## NFR

- `NFR_01`: token/secret không xuất hiện trong response, payload attempt hoặc log.
- `NFR_02`: OAuth state TTL 10 phút, dùng một lần.
- `NFR_03`: network unit tests dùng mock, không gọi Meta/Google thật.
- `NFR_04`: thay đổi additive và regression TikTok/Instagram phải pass.

## Ngoài phạm vi

Facebook profile/Group/Story/Live/Ads; YouTube Live, playlist, comment, thumbnail/caption upload;
analytics; production deploy và app review/audit.

