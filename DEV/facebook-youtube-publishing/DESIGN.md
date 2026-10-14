# Thiết kế — Facebook và YouTube publishing

- Plan/URD: `0.1`, được phép triển khai ngày `2026-10-14`.
- Không còn câu hỏi nghiệp vụ chặn.

## Mapping

| UC | Route/UI | Application/domain | Integration |
|---|---|---|---|
| UC_01 | `/facebook-auth`, `/youtube-auth` | route mỏng | OAuth/token store trong từng adapter |
| UC_02 | generic `/social-publishes` | `SocialPublishingService` | `FacebookPublisher`, `YouTubePublisher` |
| UC_03 | route cancel + scheduler hiện có | SQLite claim/idempotency hiện có | provider chỉ chạy sau claim |

## Contract và trạng thái

- Add `FACEBOOK`, `YOUTUBE` vào enum; schema text hiện tại không đổi.
- Facebook: OAuth → `/me/accounts`; start Page Reel → upload URL → finish; checkpoint video ID.
- YouTube: OAuth offline → channel discovery; resumable `videos.insert`; checkpoint video ID;
  `videos.list` map processing/publication về normalized status.
- Validation đặc thù ở adapter: account tồn tại; YouTube title bắt buộc, privacy allowlist.
- Lỗi input/config có thông báo an toàn; raw provider body/token không trả ra UI.

## Security/idempotency

State TTL 600 giây và pop một lần. Account path allowlist. Token file permission 0600 theo
convention hiện tại. Client account ID chỉ là khóa tra cứu; token phải tồn tại server-side.
Remote ID được checkpoint trước bước có thể lặp; timeout sau checkpoint chuyển reconcile.

## Test

Mock requester cho OAuth/upload/status; test state sai, account thiếu, happy path, processing,
failed và registry/routes/static UI. Regression targeted social publishing/TikTok/Instagram.

