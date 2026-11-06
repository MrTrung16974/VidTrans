# Handoff — AI social caption generation

- Ngày: 2026-11-06; local base revision `cdb21ef`.
- Plan v0.1 đã được người dùng duyệt bằng “oki bắt đầu”.
- Code/UI/tests hoàn tất local; 64/64 PASS.
- Android UI hoàn tất local: chọn tone, xem variant/tag chips, chỉ áp dụng khi người dùng bấm nút; Flutter test 11/11 PASS và debug APK build PASS.
- Deploy chưa hoàn tất: scp bị đóng rồi SSH `169.58.116.223:22` timeout; portal vẫn HTTPS 200 và chưa rebuild.
- Khi SSH phục hồi: đối chiếu checksum 8 file runtime/test/web, gửi file thiếu, chạy `docker-rebuild.sh`, unit production, smoke API/UI và thử job gần nhất.
- Không DB/config/secret change; không đọc secret.

