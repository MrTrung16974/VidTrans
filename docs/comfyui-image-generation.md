# Sinh ảnh miễn phí bằng ComfyUI

VidTrans có thể gửi prompt của từng cảnh tới một ComfyUI chạy local. Luồng mặc
định chỉ dùng node lõi của ComfyUI (`CheckpointLoaderSimple`, `KSampler`,
`SaveImage`) nên không cần cài custom node.

## Chuẩn bị

1. Cài và chạy ComfyUI với API có thể truy cập từ backend. Nếu VidTrans chạy
   bằng Docker, khởi động ComfyUI với `--listen 0.0.0.0`; không mở cổng 8188 ra
   Internet.
2. Chép một checkpoint tương thích vào `ComfyUI/models/checkpoints` rồi khởi
   động lại ComfyUI.
3. Thêm vào `.env`:

```dotenv
VIDTRANS_COMFYUI_URL=http://host.docker.internal:8188
VIDTRANS_COMFYUI_CHECKPOINT=ten-model-chinh-xac.safetensors
VIDTRANS_COMFYUI_STEPS=20
VIDTRANS_COMFYUI_CFG=7
VIDTRANS_COMFYUI_MAX_SIDE=1024
```

Nếu chạy backend trực tiếp trên máy thay vì Docker, dùng
`VIDTRANS_COMFYUI_URL=http://127.0.0.1:8188`.

Khởi động lại VidTrans, mở **Truyện → Video** và chọn **ComfyUI local (AI, miễn
phí)** tại mục **AI vẽ ảnh**. Khi ComfyUI không kết nối được hoặc model lỗi,
pipeline vẫn hoàn thành bằng ảnh minh họa tạm và ghi cảnh báo vào dự án.

## Tinh chỉnh bộ nhớ

`VIDTRANS_COMFYUI_MAX_SIDE` mặc định là 1024. Giảm xuống 768 hoặc 512 nếu GPU
thiếu VRAM. VidTrans tự upscale/crop ảnh về đúng kích thước video khi dựng.

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
