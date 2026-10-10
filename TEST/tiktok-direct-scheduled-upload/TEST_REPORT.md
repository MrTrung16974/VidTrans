# Test report

- Ngày chạy: `2026-10-10`
- Môi trường: local workspace, mock TikTok API, không dùng credential thật.

## Kết quả

- Targeted Python tests: `64 PASS`.
- Feature + publisher/store tests sau cùng: xem lệnh xác minh trong handoff.
- JavaScript syntax: PASS.
- Browser-frame tests: PASS.
- Full discovery: `Ran 204 tests`; `203` không lỗi assertion, `1 ERROR` do `test_douyin_qr_auth` import module không tồn tại.

## Giới hạn

- Chưa chạy live TikTok vì cần account OAuth test và đây là remote side effect.
- Chưa UAT thao tác thật trên trình duyệt.
- Native schedule private nằm ngoài release 0.1.
