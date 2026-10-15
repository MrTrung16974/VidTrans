# Thiết kế — Source browser login

## Kiến trúc

- `DouyinBrowserAuthManager` và `SocialBrowserAuthManager` cùng kết nối một Chromium/profile; cấu hình platform gồm start URL, domain allowlist và cookie marker.
- Giữ `DouyinBrowserAuthManager` cho resolver video đặc thù.
- `SocialVideoDownloader` nhận map provider theo platform. Thứ tự: cookie job → cookie global → provider browser → guest.
- API REST: `/api/v1/{youtube|facebook}-browser/{status,sync,reload,session}`.
- UI tái sử dụng một renderer theo platform, ba panel/tab, iframe noVNC riêng.
- Compose dùng một `social-browser`, một named profile volume và một Nginx route được bảo vệ.

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

Một sidecar giảm RAM và đơn giản hóa CA/proxy. Chuyển tab sẽ điều hướng cửa sổ hiện tại; logout phải xóa cookie theo domain để giữ phiên nền tảng khác.
