# Thiết kế — Source browser login

## Kiến trúc

- Thêm `SocialBrowserAuthManager` dùng CDP/Playwright cho YouTube và Facebook; cấu hình platform gồm start URL, domain allowlist và cookie marker.
- Giữ `DouyinBrowserAuthManager` cho resolver video đặc thù.
- `SocialVideoDownloader` nhận map provider theo platform. Thứ tự: cookie job → cookie global → provider browser → guest.
- API REST: `/api/v1/{youtube|facebook}-browser/{status,sync,reload,session}`.
- UI tái sử dụng một renderer theo platform, ba panel/tab, iframe noVNC riêng.
- Compose thêm `youtube-browser`, `facebook-browser`, named profile volumes; Nginx thêm route được bảo vệ.

## Security

- Profile/cookie cô lập theo platform; export chỉ domain allowlist.
- Không đưa cookie vào response/log. Cookie file mode 0600.
- Viewer tiếp tục bị chặn khỏi view theo logic role hiện hành.
- Logout xóa cookies và web storage trong đúng sidecar.

## Test strategy

- Unit: nhận diện auth marker, domain filter, sync/logout, downloader chọn đúng provider và xóa snapshot.
- Static UI: tab/control/route được ship.
- Compose config và Python unittest liên quan.

## Tradeoff

Hai sidecar bổ sung dùng nhiều RAM hơn một profile dùng chung, đổi lại cô lập phiên và logout an toàn, không làm điều hướng nền tảng này phá phiên nền tảng khác.
