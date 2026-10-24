# Handoff — Tích hợp ComfyUI với VidTrans

- Workspace: VidTrans
- Feature: `comfyui-integration`
- Cập nhật: 2026-10-24
- Giai đoạn: local integration hoàn tất; full comic UAT chưa chạy

## Đọc tiếp theo thứ tự

1. `BA_URD/comfyui-integration/ANALYSIS.md`
2. `TEST/comfyui-integration/TEST_REPORT.md`
3. `DEV/comfyui-integration/IMPLEMENTATION.md`
4. `DEPLOY/comfyui-integration/RUNBOOK.md`

## Hiện trạng đã xác minh

- Adapter ComfyUI, UI selector, Docker variables, fallback và unit test đã tồn tại.
- ComfyUI 0.36.0 đã cài local; smoke `/system_stats` thành công với Apple MPS.
- SDXL Base 1.0 đã được tải từ kho chính thức Stability AI/Hugging Face và metadata safetensors hợp lệ.
- Plan version 0.1 được người dùng duyệt ngày 2026-10-24 bằng xác nhận “oki bắt đầu”.
- Adapter production hiện hữu đáp ứng design; bổ sung 4 test lỗi/timeout/config, tổng provider suite 6/6 PASS.
- Readiness `/system_stats` PASS với ComfyUI 0.36.0 và Apple MPS.

## Thay đổi cần giữ nguyên

- Workspace đã có thay đổi ngoài feature ở `.DS_Store`, `comfyui/comfyui.log` và `comfyui/comfyui.pid`; không sửa/xóa như một phần coding feature.

## Blocker/quyết định

- Không còn blocker cho local image provider. Comic video hoàn chỉnh vẫn cần UAT riêng nếu người dùng yêu cầu.

## Bước kế tiếp

Refresh UI VidTrans, xác nhận ComfyUI selectable, sau đó có thể chạy một comic preview/full job UAT.

## Ràng buộc bảo mật

Không lưu secret/giá trị credential. Chỉ dùng tên biến `VIDTRANS_COMFYUI_URL`, `VIDTRANS_COMFYUI_CHECKPOINT`, `VIDTRANS_COMFYUI_STEPS`, `VIDTRANS_COMFYUI_CFG`, `VIDTRANS_COMFYUI_SAMPLER`, `VIDTRANS_COMFYUI_SCHEDULER`, `VIDTRANS_COMFYUI_MAX_SIDE`.
