# Manifest — ComfyUI integration

- Version: local working tree, 2026-10-24
- Trạng thái: `LOCAL READY — unit/readiness/real image smoke PASS`

| Artifact | Mục đích | Trạng thái |
|---|---|---|
| `backend/infrastructure/image_providers.py` | Production adapter hiện hữu | Verified by unit tests; không sửa |
| `backend/tests/test_image_providers.py` | Provider verification | Modified; 6/6 PASS |
| `comfyui/models/checkpoints/sd_xl_base_1.0.safetensors` | Local SDXL checkpoint | Downloaded; safetensors metadata valid, 2,515 tensors |
| `comfyui/output/vidtrans-comfyui-smoke.png` | End-to-end smoke artifact | PASS, 768x768 |
| `BA_URD/comfyui-integration/*` | Requirement/traceability/handoff | Created |
| `DEV/comfyui-integration/*` | Design/implementation evidence | Created |
| `TEST/comfyui-integration/*` | Test cases/report | Created |
| `DEPLOY/comfyui-integration/RUNBOOK.md` | Local operation/rollback | Created |
| SQL | Không đổi DB | N/A |
| Camunda | Không dùng workflow engine | N/A |
| BM01/BM02 XLSX | Không có yêu cầu triển khai production/CAB trong phạm vi local | N/A |

Checksum chưa chốt vì working tree chưa được đóng gói/commit. Không triển khai production từ manifest này.

