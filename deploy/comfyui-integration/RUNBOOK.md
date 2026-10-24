# Runbook local — VidTrans + ComfyUI

## Điều kiện trước

- macOS Apple Silicon, `git` và `uv` có sẵn.
- Một checkpoint tương thích workflow core-node đã được người vận hành duyệt license và đặt tại `comfyui/models/checkpoints/`.
- Cổng 8188 chỉ dùng local/LAN tin cậy, không được NAT/public ra Internet.

## Khởi động ComfyUI

```bash
./deploy/comfyui-local.sh install
./deploy/comfyui-local.sh start
./deploy/comfyui-local.sh status
```

Health check:

```bash
curl -fsS http://127.0.0.1:8188/system_stats
```

## Cấu hình runtime VidTrans

Các giá trị do người vận hành cung cấp ngoài source:

- Backend native: `VIDTRANS_COMFYUI_URL=http://127.0.0.1:8188`
- Backend Docker Desktop: `VIDTRANS_COMFYUI_URL=http://host.docker.internal:8188`
- `VIDTRANS_COMFYUI_CHECKPOINT=<đúng tên file checkpoint>`
- Các tuning optional: steps, CFG, sampler, scheduler, max side.

Không commit file cấu hình chứa giá trị môi trường. Restart backend sau khi cập nhật runtime config.

## Smoke test

1. Gọi comic options và xác nhận ComfyUI `configured=true`.
2. Mở **Truyện → Video**, chọn **ComfyUI local (AI, miễn phí)**.
3. Tạo preview bằng prompt synthetic, một cảnh, quality draft.
4. Xác nhận job completed hoặc completed-with-fallback; nếu thành công, ảnh là PNG thật đúng kích thước.
5. Kiểm tra manifest `image_fallback`; giá trị có count > 0 nghĩa dependency/model đã lỗi và smoke chưa đạt mục tiêu ảnh thật.

## Tiêu chí dừng/rollback

- Dừng nếu checkpoint load error, MPS out-of-memory lặp lại, cổng bị expose ngoài boundary hoặc output chỉ fallback.
- Giảm `VIDTRANS_COMFYUI_MAX_SIDE` xuống 768/512 khi thiếu memory, rồi retry một lần có kiểm soát.
- Rollback ứng dụng: chọn provider placeholder/Gemini theo cấu hình hiện hữu.
- Dừng runtime:

```bash
./deploy/comfyui-local.sh stop
```

Không có DB migration/SQL/Camunda cần rollback.

