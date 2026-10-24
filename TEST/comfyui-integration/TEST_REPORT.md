# Test report — ComfyUI integration

- Ngày chạy: 2026-10-24
- Build: working tree local, chưa commit
- Testcase: `TESTCASES.md` v0.1

## Tổng hợp

| Trạng thái | Số lượng |
|---|---:|
| PASS | 10 |
| FAIL | 0 |
| BLOCKED | 0 |
| NOT_RUN | 1 |

## Kết quả

- Unit provider: 6/6 PASS trong `comfyui/app/.venv`.
- Syntax compile: PASS cho provider và test file.
- Runtime readiness: PASS, ComfyUI 0.36.0 trả thiết bị `mps`.
- Real image generation: PASS qua chính `ComfyUIImageProvider` trong container VidTrans; SDXL Base 1.0 sinh PNG 768x768, 746,078 bytes, khoảng 49 giây.
- Comic API regression: NOT_RUN vì Python local thiếu full dependency set; lỗi thử ban đầu là `ModuleNotFoundError: PIL`, không phải lỗi assertion/source.

Real provider smoke không được coi là UAT hoặc bằng chứng một comic video hoàn chỉnh đã render toàn bộ cảnh/voice/video.

