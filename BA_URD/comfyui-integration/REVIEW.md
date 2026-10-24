# Review — ComfyUI integration

| RV ID | Artifact/vị trí | Finding | Mức độ | Cách xử lý | Trạng thái | Bằng chứng |
|---|---|---|---|---|---|---|
| RV_01 | Existing integration | Không cần viết lại adapter; source đã có đầy đủ queue/poll/download/fallback | Info | Tái sử dụng, chỉ sửa khi test phát hiện defect | CLOSED | `image_providers.py`, `comic_video.py` |
| RV_02 | Runtime | Chưa có checkpoint nên chưa thể gọi workflow thật | High | Tải SDXL Base 1.0 và chạy production adapter smoke | CLOSED | PNG 768x768 sinh thành công ngày 2026-10-24 |
| RV_03 | Network | `0.0.0.0:8188` cần cho Docker nhưng có rủi ro LAN exposure | Medium | Runbook yêu cầu không public Internet và giới hạn host/firewall | MITIGATED | `DEPLOY/comfyui-integration/RUNBOOK.md` |

