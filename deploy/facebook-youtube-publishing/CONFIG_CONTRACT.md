# Contract cấu hình

Không ghi giá trị secret vào repository. Cấp các biến sau qua secret/environment manager:

- `VIDTRANS_FACEBOOK_CLIENT_ID`, `VIDTRANS_FACEBOOK_CLIENT_SECRET`,
  `VIDTRANS_FACEBOOK_REDIRECT_URI`; optional `VIDTRANS_FACEBOOK_API_BASE`.
- `VIDTRANS_YOUTUBE_CLIENT_ID`, `VIDTRANS_YOUTUBE_CLIENT_SECRET`,
  `VIDTRANS_YOUTUBE_REDIRECT_URI`.

Redirect URI phải HTTPS và khớp chính xác callback đã khai báo. Facebook cần Page permissions;
Google project cần bật YouTube Data API và OAuth consent phù hợp.

