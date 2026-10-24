# Test cases — ComfyUI integration

- Feature: `comfyui-integration`
- URD: v0.1
- Môi trường: macOS Apple Silicon, ComfyUI local 0.36.0, synthetic prompt/data

| TC ID | UC/BR/AC | Nhóm/Priority | Tiền điều kiện | Bước | Kết quả mong đợi | Trạng thái | Bằng chứng |
|---|---|---|---|---|---|---|---|
| TC_001 | UC_01/AC01_01 | Config/P1 | URL + checkpoint synthetic | Khởi tạo provider | Provider hợp lệ | PASS | unit test workflow happy path |
| TC_002 | UC_01/AC01_02 | Validation/P1 | Thiếu URL/checkpoint | Khởi tạo provider | Báo thiếu cấu hình | PASS | `test_requires_url_and_checkpoint` |
| TC_003 | UC_02/BR02_01 | Functional/P1 | Mock ComfyUI | Dựng workflow 9:16 | Kích thước latent 576x1024, checkpoint đúng | PASS | `test_generates_and_downloads_comfyui_result` |
| TC_004 | UC_02/AC02_03 | Idempotency/P1 | Seed 0 | Dựng workflow 2 lần | Seed giống nhau | PASS | `test_workflow_uses_deterministic_seed_when_zero` |
| TC_005 | UC_02/AC02_01 | Functional/P1 | Mock prompt/history/view | Generate | PNG đúng 1080x1920 | PASS | `test_generates_and_downloads_comfyui_result` |
| TC_006 | UC_02/EF02_01 | Dependency/P1 | Connection refused | Generate | Lỗi hành động được, `unavailable=true` | PASS | `test_connection_error_marks_provider_unavailable` |
| TC_007 | UC_02/EF02_02 | Error/P1 | History status error | Poll | Báo execution error, không tải view | PASS | `test_history_error_is_reported_without_downloading` |
| TC_008 | UC_02/BR02_03 | Timeout/P1 | Deadline 0 | Generate | Dừng hữu hạn với timeout | PASS | `test_poll_timeout_is_bounded` |
| TC_009 | NFR_05 | Readiness/P1 | ComfyUI đang chạy | GET `/system_stats` | Version/device trả về | PASS | 0.36.0/mps ngày 2026-10-24 |
| TC_010 | UC_02/AC02_01 | Real smoke/P1 | SDXL Base 1.0 | Generate synthetic prompt qua production adapter trong container VidTrans | PNG thật 768x768 | PASS | `comfyui/output/vidtrans-comfyui-smoke.png` |
| TC_011 | UC_01/AC01_01 | API regression/P2 | VidTrans test runtime đầy đủ | Chạy comic API tests | Options/config validation PASS | NOT_RUN | Local Python thiếu dependency dự án |

