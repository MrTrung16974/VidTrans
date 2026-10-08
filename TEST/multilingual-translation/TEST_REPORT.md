# Test report — Dịch đa ngôn ngữ

- Ngày chạy: 2026-10-08
- Môi trường: macOS host và Docker local `vidtrans`
- Phạm vi: catalog/domain, ASR, translation, compatibility, frontend syntax, Docker/font/startup.

## Kết quả

| Hạng mục | Kết quả | Bằng chứng |
|---|---|---|
| Unit feature trên host | PASS | 31/31 test |
| JavaScript syntax | PASS | `node --check` |
| Patch hygiene | PASS | `git diff --check` |
| Full Docker build | PASS | `docker-rebuild.sh --full --local` |
| Startup | PASS | app và hai browser container chạy; browser healthy |
| Font Thái/CJK | PASS | `fc-match` trả Noto tương ứng trong container cuối |
| Import ứng dụng | PASS | `import main` |
| Unit feature trong image | PASS | 31/31 test trong container cuối |

## Regression toàn repo

Lần chạy host trước đó thu được 181 test nhưng không xanh hoàn toàn vì ba vấn đề ngoài feature: module `infrastructure.douyin_qr_auth` không tồn tại, host thiếu `python-multipart`, và một test auth token hết hạn/tampered bị failure. Các lỗi này không nằm trên đường code đa ngôn ngữ; suite chọn lọc và image Docker có dependency thật đều được chạy riêng.

## Kết luận

Feature đủ điều kiện chạy local và chuẩn bị triển khai theo runbook. Chưa triển khai VPS trong phạm vi lần này.
