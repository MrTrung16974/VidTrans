# Kế hoạch — ComfyUI + SDXL cho tạo ảnh truyện trên máy 8GB RAM

- Feature: `comfyui-sdxl-comic`
- Version: `0.1`
- Trạng thái: `DRAFT` — Q_01–Q_04 đã chốt 2026-11-06; chờ chọn hướng triển khai Q_05 (mục 8)
- Kế thừa: `BA_URD/comfyui-integration` (v0.1, DONE cho local Mac/MPS)
- Máy đích giả định: Linux, 8GB RAM, 100GB disk, chạy cùng stack Docker Compose của VidTrans

## 0. Hiện trạng (đã có, KHÔNG làm lại)

| Hạng mục | Trạng thái | Bằng chứng |
|---|---|---|
| ComfyUI service Docker, pin v0.36.0, torch cu126, profile `comfyui` | Có | `comfyui/Dockerfile`, `docker-compose.yml` |
| Adapter `ComfyUIImageProvider` (submit/poll/view, cancel, fallback placeholder) | Có | `backend/infrastructure/image_providers.py:156` |
| IP-Adapter SDXL giữ mặt nhân vật + bối cảnh | Có | node `IPAdapterUnifiedLoader`/`IPAdapter`, model `ip-adapter_sdxl_vit-h` |
| Checkpoint SDXL Juggernaut XL v9 (6.6GB) | Có trên Mac | `comfyui/models/checkpoints/` |
| Port 8188 chỉ bind 127.0.0.1 | Có | `docker-compose.yml` |
| ComfyUI-Manager | Cố ý KHÔNG cài | Pin custom node trong Dockerfile để build tái lập được; giữ nguyên |

Vì vậy kế hoạch trong tin nhắn gốc (clone ComfyUI, venv, workflow mặc định) phần lớn đã xong. Phần còn thiếu là **làm SDXL chạy ổn định khi chỉ có 8GB RAM dùng chung với Whisper/OCR/Postgres**.

## 1. Vấn đề cần giải quyết

1. **Tranh chấp RAM.** SDXL fp16 cần nạp CLIP ~1.6GB + UNet ~4.9GB, IP-Adapter thêm CLIP-Vision ~2.4GB + 0.7GB. Container `vidtrans` (faster-whisper, Paddle OCR) + Postgres cùng chạy → 8GB không đủ. Log Mac đã thấy dấu hiệu: bước 16/28 bị kẹt 16 phút (`293 s/it`) do memory pressure.
2. **Cờ khởi động cứng.** `CMD` trong Dockerfile hardcode `--lowvram`; không đổi được theo GPU nếu không sửa image.
3. **Độ phân giải không hợp SDXL.** Compose mặc định `MAX_SIDE=640` + checkpoint SD1.5 (tối ưu cho GTX 1650). SDXL vẽ ở ~1MP; 640px làm ảnh xấu, méo bố cục.
4. **Không có VAE riêng.** Chưa hỗ trợ `sdxl-vae-fp16-fix` → rủi ro ảnh đen/NaN khi chạy fp16 trên GPU NVIDIA.
5. **Readiness hời hợt.** `configured` chỉ kiểm tra biến môi trường; checkpoint sai tên hoặc thiếu file IP-Adapter chỉ lộ ra khi đang render → cả truyện rơi về ảnh tạm.
6. **Disk.** `ltxv-2b` (5.9GB) + `t5xxl_fp16` (9.1GB) phục vụ video provider; cần quyết định giữ hay bỏ trên máy 100GB.
7. **Timeout cứng 300 s/ảnh.** Đủ cho GPU, không đủ cho lần nạp model đầu (swap) hay chế độ CPU.

## 2. Phương án theo phần cứng (chốt sau Q_01)

| Profile | Phần cứng | Cờ ComfyUI | Model/độ phân giải | Ước tính/ảnh |
|---|---|---|---|---|
| A | GPU ≥ 8GB VRAM | `--normalvram` | SDXL đầy đủ, bucket 768×1344 (9:16), 25–28 steps, dpmpp_2m karras, CFG 5–6 | 15–40 s |
| B | GPU 4–6GB VRAM (vd GTX 1650) | `--lowvram` | SDXL bucket 832×1216 hoặc nhỏ hơn; khuyên thêm LoRA SDXL-Lightning 8 steps, CFG 1.5–2 | 1–3 phút |
| C | Không GPU | `--cpu` | Chỉ SDXL-Lightning 4 steps, tắt IP-Adapter; thực tế nên giữ provider cloud (Pollinations/Cloudflare) | 3–10 phút, không khuyến nghị cho truyện nhiều cảnh |

Mọi profile: cùng một workflow, chỉ khác biến môi trường — không rẽ nhánh code theo máy.

## 3. Các bước thực hiện

| Bước | Việc làm | File đầu ra | Kiểm chứng | Trạng thái |
|---|---|---|---|---|
| 1. Chuẩn bị host | Swap 16GB + `vm.swappiness=10`; NVIDIA driver + `nvidia-container-toolkit` (profile A/B); kiểm tra `nvidia-smi` trong container | `deploy/comfyui-host-setup.sh` (idempotent), runbook | `free -h`, `docker run --gpus all ... nvidia-smi` | TODO |
| 2. Cờ khởi động cấu hình được | Đổi `CMD` sang đọc `COMFYUI_EXTRA_ARGS` (mặc định `--lowvram`); thêm cờ tiết kiệm RAM (`--cache-none` hoặc `--cache-lru` nhỏ); đặt `mem_limit` cho service | `comfyui/Dockerfile`, `docker-compose.yml`, `docker-compose.gpu.yml` | Container khởi động với từng profile; `/system_stats` trả đúng device | TODO |
| 3. Giải phóng RAM giữa các job | Sau khi comic job xong phần ảnh (kể cả lỗi/huỷ), gọi `POST /free {"unload_models": true, "free_memory": true}` để Whisper/OCR lấy lại RAM | `image_providers.py` (`release()`), chỗ gọi trong comic job runner | Unit test gọi `/free` đúng 1 lần/job; đo RSS container trước/sau | TODO |
| 4. Không chạy chồng job nặng | Một "heavy slot" dùng chung: phase sinh ảnh ComfyUI và ASR/OCR không chạy đồng thời trên máy 8GB (bật bằng `VIDTRANS_HEAVY_JOB_SLOTS=1`); các job nhẹ vẫn song song | `backend/infrastructure/job_store.py` hoặc lock trong runner | Test: 1 job comic + 1 job dịch video → chạy tuần tự phần nặng, không OOM | TODO |
| 5. Độ phân giải theo bucket SDXL | `VIDTRANS_COMFYUI_RESOLUTION=sdxl` chọn bucket gần tỉ lệ nhất (1024², 832×1216, 768×1344, 1216×832, 1344×768); giữ `max_side` cho SD1.5; đưa vào `cache_key` | `image_providers.py` | Unit test 9:16/16:9/1:1 ra đúng bucket, chia hết cho 64 | TODO |
| 6. VAE + LoRA tuỳ chọn | `VIDTRANS_COMFYUI_VAE` → thêm `VAELoader`; `VIDTRANS_COMFYUI_LORAS` (vd `lightning_8step.safetensors:1.0`) → chuỗi `LoraLoader`; cả hai vào `cache_key` | `image_providers.py` | Unit test shape workflow có/không VAE, có/không LoRA, kết hợp IP-Adapter | TODO |
| 7. Readiness thật | Kiểm tra qua `GET /object_info/CheckpointLoaderSimple` (và VAELoader, IPAdapter) rằng file model tồn tại; options trả `ready/model_missing/unreachable` kèm lý do; render dừng sớm thay vì vẽ toàn ảnh tạm | `image_providers.py` (`image_provider_statuses`), `comic_routes` options, UI chọn provider | Test API: thiếu checkpoint → `model_missing`, UI hiển thị cảnh báo | TODO |
| 8. Timeout & tiến độ | `VIDTRANS_COMFYUI_TIMEOUT` (mặc định 300, profile C 900); lần ảnh đầu tiên được cộng thêm thời gian nạp model | `image_providers.py`, compose | Unit test deadline theo cấu hình | TODO |
| 9. Quản lý model & disk | Script tải model có checksum sha256 vào đúng thư mục, liệt kê dung lượng; danh mục model tối thiểu cho truyện (checkpoint chính, VAE fp16-fix, IP-Adapter + CLIP-Vision, LoRA Lightning tuỳ profile); quyết định ltxv/t5xxl theo Q_03 | `deploy/comfyui-models.sh`, `docs/comfyui-image-generation.md` | Chạy lại script không tải trùng; sai checksum → báo lỗi | TODO |
| 10. Cập nhật tài liệu | Viết lại doc theo SDXL (doc hiện tại còn ghi SD1.5 và "chưa có IP-Adapter" — đã lỗi thời); runbook triển khai, SSH tunnel `ssh -L 8188:localhost:8188`, rollback | `docs/comfyui-image-generation.md`, `DEPLOY/comfyui-sdxl-comic/RUNBOOK.md` | Review đối chiếu biến môi trường thật trong compose | TODO |
| 11. TEST & benchmark | Unit + regression + smoke thật + UAT 1 truyện ~10 cảnh trên máy đích; ghi bảng s/ảnh, RAM đỉnh, swap dùng, số ảnh fallback | `TEST/comfyui-sdxl-comic/TESTCASES.md`, `TEST_REPORT.md` | Xem mục 5 | TODO |
| 12. Handoff | Tổng kết, giới hạn, bước tiếp | `BA_URD/comfyui-sdxl-comic/HANDOFF.md` | Đối chiếu git diff + test evidence | TODO |

Thứ tự: 1 → 2 → 5/6 → 7 → 3/4 → 8 → 9/10 → 11 → 12. Bước 5–8 làm được ngay trên Mac (unit test, không cần máy đích); bước 1, 2, 11 cần máy đích.

## 4. Biến môi trường mới (dự kiến)

| Biến | Mặc định | Mục đích |
|---|---|---|
| `COMFYUI_EXTRA_ARGS` | `--lowvram` | Cờ ComfyUI theo profile |
| `VIDTRANS_COMFYUI_RESOLUTION` | `max_side` | `sdxl` để dùng bucket SDXL |
| `VIDTRANS_COMFYUI_VAE` | rỗng | Tên file VAE (vd `sdxl_vae_fp16_fix.safetensors`) |
| `VIDTRANS_COMFYUI_LORAS` | rỗng | Danh sách `file:weight`, phân tách bằng dấu phẩy |
| `VIDTRANS_COMFYUI_TIMEOUT` | `300` | Giây chờ tối đa mỗi ảnh |
| `VIDTRANS_COMFYUI_FREE_AFTER_JOB` | `true` | Gọi `/free` sau mỗi job |
| `VIDTRANS_HEAVY_JOB_SLOTS` | không giới hạn | `1` trên máy 8GB |

Mặc định giữ hành vi hiện tại để máy đang chạy không đổi kết quả; profile 8GB bật bằng `.env`.

## 5. Test strategy

1. **Unit** (`backend/tests/test_image_providers.py`): bucket SDXL, VAE/LoRA trong workflow, cache_key đổi khi cấu hình đổi, `/free` được gọi kể cả khi lỗi/huỷ, readiness `model_missing`, timeout cấu hình.
2. **Regression**: toàn bộ provider suite + `test_comic_api.py` + `test_plan_entitlements.py`.
3. **Smoke thật trên máy đích**: `/system_stats`, 1 ảnh không tham chiếu, 1 ảnh có IP-Adapter, kiểm tra PNG đúng kích thước, không đen.
4. **Áp lực RAM**: chạy 1 comic job song song 1 job dịch video; đạt khi không OOM-kill, không bước sampler nào > 30 s/it sau ảnh đầu.
5. **UAT**: 1 truyện ~10 cảnh, 2–3 nhân vật; tiêu chí: 0 ảnh fallback, nhân vật nhận ra được giữa các cảnh, thời gian tổng nằm trong ước tính profile.

## 6. Rủi ro & rollback

- **8GB vẫn có thể không đủ cho IP-Adapter + SDXL cùng lúc** → adapter đã có đường retry không IP-Adapter; nếu benchmark xấu, profile B/C tắt IP-Adapter mặc định.
- **Swap trên disk chậm** làm lần nạp đầu 1–3 phút → chấp nhận, đã tính vào timeout ảnh đầu.
- **License model**: không tự tải/chấp nhận license thay người dùng; script tải chỉ chạy khi người dùng chạy.
- **Không đánh đổi chất lượng lấy tốc độ một cách âm thầm**: Lightning LoRA/giảm steps chỉ bật theo profile và ghi rõ trong manifest.
- **Rollback code**: các biến mới mặc định = hành vi cũ; revert file feature là đủ. **Rollback runtime**: `docker compose stop comfyui`, chọn lại provider cloud/placeholder.

## 7. Câu hỏi mở

| Q ID | Nội dung | Ảnh hưởng | Trạng thái |
|---|---|---|---|
| Q_01 | Máy đích có GPU NVIDIA không, VRAM bao nhiêu? | Chọn profile A/B/C | RESOLVED 2026-11-06 — **không có GPU**: `nvidia-smi` không có, `lspci` chỉ thấy `1234:1111` (card VGA ảo của QEMU). Máy đích rơi vào **profile C** |
| Q_02 | Giữ Juggernaut XL hay thêm checkpoint anime/manhua? | Disk, chất lượng style vẽ tay | RESOLVED — giữ Juggernaut XL làm checkpoint chính; style vẽ tay dùng LoRA khi cần, không thêm checkpoint |
| Q_03 | Có cần LTXV (video) trên máy 8GB không? | Disk, bước 9 | RESOLVED — bỏ `ltxv-2b` + `t5xxl_fp16` trên máy đích (~15GB) |
| Q_04 | Có chấp nhận SDXL-Lightning LoRA không? | Thời gian vs chất lượng | RESOLVED — chấp nhận khi GPU yếu/không GPU; GPU ≥8GB dùng Juggernaut thường trước |
| Q_05 | Máy đích không có GPU: chọn hướng triển khai nào (xem mục 8)? | Quyết định toàn bộ bước 1–4, 11 | OPEN |

## 8. Hệ quả của Q_01: VPS chỉ có CPU

SDXL trên CPU chạy fp32: riêng UNet ~10GB, cộng CLIP ~1.6GB → **vượt 8GB RAM**, phải swap liên tục. Ước tính thực tế 5–15 phút/ảnh với 25 steps; một truyện 30–70 cảnh mất nhiều giờ và chiếm hết RAM của Whisper/OCR. IP-Adapter (thêm ~3GB) gần như không chạy được. Vì vậy profile C không phù hợp làm đường chính.

| Hướng | Mô tả | Thay đổi code | Ước tính/ảnh | Ghi chú |
|---|---|---|---|---|
| **H1 (khuyến nghị)** ComfyUI trên máy có GPU, VPS gọi qua tunnel | ComfyUI chạy trên Mac (MPS, đã chạy được ~2 phút/ảnh) hoặc PC NVIDIA; nối VPS ↔ máy đó bằng Tailscale hoặc SSH reverse tunnel (`ssh -R 8188:localhost:8188 root@VPS`). Adapter đã nhận URL bất kỳ | Gần như không: readiness (bước 7), timeout (bước 8), mã hoá kết nối nằm ở tunnel | 30 s–2 phút | Máy GPU phải bật khi render; khi tắt → readiness báo `unreachable`, fallback sang cloud |
| H2 Thuê GPU theo giờ | Pod ComfyUI trên RunPod/Vast…, cùng image `comfyui/Dockerfile`, bật khi cần | Như H1 + script bật/tắt pod | 15–40 s | Tốn tiền theo giờ; model phải nằm trên volume của pod |
| H3 Chạy CPU trên VPS | SDXL-Lightning 4 steps, ~768px, tắt IP-Adapter, `--cpu`, heavy slot = 1, swap 16GB | Toàn bộ bước 1–8 | 3–8 phút (phải benchmark) | Nhân vật không còn được giữ mặt bằng IP-Adapter; chặn job dịch video trong lúc vẽ |
| H4 Giữ provider cloud trên VPS | Pollinations có `POLLINATIONS_TOKEN`, Cloudflare, hoặc bật billing Gemini | Không | vài giây | Không phải ComfyUI; phụ thuộc quota bên ngoài |

H1 và H4 kết hợp được: ComfyUI là provider chính khi máy GPU online, cloud là dự phòng.
