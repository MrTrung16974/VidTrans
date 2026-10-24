# Sinh ảnh miễn phí bằng ComfyUI

VidTrans có thể gửi prompt của từng cảnh tới một ComfyUI chạy local. Luồng mặc
định chỉ dùng node lõi của ComfyUI (`CheckpointLoaderSimple`, `KSampler`,
`SaveImage`) nên không cần cài custom node.

## Chạy cùng Docker Compose (Windows + NVIDIA)

Repo đã có service `comfyui`, dùng GPU NVIDIA và kết nối trực tiếp với backend
qua `http://comfyui:8188`. Tải checkpoint FP16 vào đúng thư mục rồi build:

```powershell
curl.exe -L --retry 5 -C - -o comfyui/models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors `
  https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/v1-5-pruned-emaonly-fp16.safetensors
docker compose up -d --build comfyui vidtrans web
```

Giao diện ComfyUI chỉ mở trên máy local tại `http://127.0.0.1:8188`. Cấu hình
mặc định dùng `--lowvram`, cạnh dài 640 px, 24 bước và sampler DPM++ 2M Karras.
Đây là mức tăng vừa phải trên GTX 1650 4 GB; ảnh dọc sinh ở khoảng 360×640 rồi
được upscale khi dựng video.

## Kết nối ComfyUI chạy ngoài Compose

1. Cài và chạy ComfyUI với API có thể truy cập từ backend. Nếu VidTrans chạy
   bằng Docker, khởi động ComfyUI với `--listen 0.0.0.0`; không mở cổng 8188 ra
   Internet.
2. Chép một checkpoint tương thích vào `ComfyUI/models/checkpoints` rồi khởi
   động lại ComfyUI.
3. Thêm vào `.env`:

```dotenv
VIDTRANS_COMFYUI_URL=http://host.docker.internal:8188
VIDTRANS_COMFYUI_CHECKPOINT=ten-model-chinh-xac.safetensors
VIDTRANS_COMFYUI_STEPS=24
VIDTRANS_COMFYUI_CFG=6.5
VIDTRANS_COMFYUI_SAMPLER=dpmpp_2m
VIDTRANS_COMFYUI_SCHEDULER=karras
VIDTRANS_COMFYUI_MAX_SIDE=640
```

Nếu chạy backend trực tiếp trên máy thay vì Docker, dùng
`VIDTRANS_COMFYUI_URL=http://127.0.0.1:8188`.

Khởi động lại VidTrans, mở **Truyện → Video** và chọn **ComfyUI local (AI, miễn
phí)** tại mục **AI vẽ ảnh**. Khi ComfyUI không kết nối được hoặc model lỗi,
pipeline vẫn hoàn thành bằng ảnh minh họa tạm và ghi cảnh báo vào dự án.

## Tinh chỉnh bộ nhớ

`VIDTRANS_COMFYUI_MAX_SIDE` mặc định trong Compose là 640. Có thể tăng lên 768
nếu GPU đủ VRAM; VidTrans tự upscale/crop ảnh về đúng kích thước video khi dựng.

Provider mặc định chưa nhận ảnh tham chiếu nhân vật. Cùng seed và mô tả nhân vật
giúp hình ổn định hơn, nhưng độ nhất quán sẽ kém Gemini cho tới khi có workflow
IP-Adapter/LoRA tùy chỉnh.

## Cài local cô lập trên macOS

Script quản lý giữ toàn bộ ứng dụng, virtualenv, cache, model và log trong thư
mục `comfyui/`; không cài package vào Python hệ thống:

```bash
./deploy/comfyui-local.sh install
./deploy/comfyui-local.sh start
./deploy/comfyui-local.sh status
./deploy/comfyui-local.sh logs
./deploy/comfyui-local.sh stop
```

`uninstall` xóa ComfyUI, Python virtualenv và cache nhưng giữ model đã tải:

```bash
./deploy/comfyui-local.sh uninstall
```

`purge` yêu cầu nhập `DELETE` rồi mới xóa cả model:

```bash
./deploy/comfyui-local.sh purge
```

ComfyUI chạy native qua Metal/MPS. Khi VidTrans chạy trong Docker Desktop, dùng
`VIDTRANS_COMFYUI_URL=http://host.docker.internal:8188`.
