# Open questions — ComfyUI integration

| Q ID | Nguồn/mục | Nội dung | Ảnh hưởng | Người xác nhận | Trạng thái | Quyết định/bằng chứng |
|---|---|---|---|---|---|---|
| Q_01 | ANALYSIS/9 | Chọn checkpoint nào, nguồn/license/dung lượng nào được chấp nhận? | Chặn real image smoke | Người dùng | RESOLVED | Người dùng ủy quyền “tự chọn model”; chọn SDXL Base 1.0 từ Stability AI, CreativeML Open RAIL++-M |
| Q_02 | ANALYSIS/9 | Local target chính là native hay Docker Compose? | Chỉ ảnh hưởng URL smoke/runbook; không chặn unit test | Người dùng | RESOLVED | Runtime thực tế là Docker Compose; dùng `host.docker.internal:8188` |

