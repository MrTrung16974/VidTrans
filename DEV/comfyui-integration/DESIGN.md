# Design — ComfyUI integration

- Plan: `BA_URD/comfyui-integration/PLAN.md` v0.1, approved 2026-10-24
- URD: `BA_URD/comfyui-integration/URD.md` v0.1
- Trạng thái: ready for verification; Q_01 chỉ chặn real smoke

## 1. Kiến trúc và mapping

| Requirement | API/controller | Application/worker | Integration/domain | Test |
|---|---|---|---|---|
| UC_01/BR01_01 | `comic_routes.options` | — | `image_provider_statuses` | `test_comic_api.py` |
| UC_01/BR01_02 | `comic_routes.create_project` | queue hiện hữu | `ComicSettings` + entitlements | `test_comic_api.py` |
| UC_02/BR02_01-04 | — | `ComicJobRunner.run` | `ComfyUIImageProvider` | `test_image_providers.py` |
| UC_02/EF02_01 | — | `ComicVideoProducer` | `ImageGenerationError` + placeholder | `test_comic_video.py` |

Route giữ mỏng; provider sở hữu HTTP/files; pipeline sở hữu fallback và cache. Không thay đổi public API contract.

## 2. Contract ComfyUI

- Submit: `POST {base_url}/prompt`, JSON `{prompt: <workflow>}`.
- Completion: poll `GET {base_url}/history/{prompt_id}`.
- Artifact: `GET {base_url}/view?filename=...&subfolder=...&type=...`.
- Workflow dùng `CheckpointLoaderSimple`, `CLIPTextEncode`, `EmptyLatentImage`, `KSampler`, `VAEDecode`, `SaveImage`.
- Ảnh được `fit_cover` sang kích thước video rồi ghi atomic.

## 3. Cấu hình

| Biến | Kiểu/default | Mục đích |
|---|---|---|
| `VIDTRANS_COMFYUI_URL` | URL, required khi chọn provider | Native: loopback; Docker: host gateway |
| `VIDTRANS_COMFYUI_CHECKPOINT` | filename, required | Tên checkpoint mà ComfyUI liệt kê |
| `VIDTRANS_COMFYUI_STEPS` | int/20 | Sampling steps |
| `VIDTRANS_COMFYUI_CFG` | float/7 | CFG scale |
| `VIDTRANS_COMFYUI_SAMPLER` | string/euler | Sampler name |
| `VIDTRANS_COMFYUI_SCHEDULER` | string/normal | Scheduler name |
| `VIDTRANS_COMFYUI_MAX_SIDE` | int/1024 | Giới hạn latent để giảm VRAM |

Giá trị được runtime cung cấp ngoài source. Không đọc, ghi hoặc sao chép file secret/config bị hạn chế.

## 4. Lỗi, retry và idempotency

- HTTP connection lỗi đặt `unavailable=true`; producer chỉ probe một request trước khi fan-out.
- Poll không resubmit prompt, nên tránh duplicate side effect.
- Timeout kết thúc bằng fallback; ảnh placeholder và manifest ghi lý do.
- Cache key chứa checkpoint và sampling config để không tái dùng artifact từ model/config khác.

## 5. Security và tenancy

- Quyền project/tenant tiếp tục dùng route/job ownership hiện hữu; ComfyUI không nhận user identity.
- Prompt là nội dung job; chỉ gửi tới dependency local do operator cấu hình.
- Không public port 8188; không đưa credential vào query/log.
- Không đổi DB, transaction hoặc object authorization.

## 6. Chiến lược triển khai nhỏ nhất

1. Chạy test hiện hữu trước.
2. Nếu test xanh và contract đáp ứng URD, không sửa source chỉ để tạo diff.
3. Chỉ bổ sung test hoặc sửa defect có bằng chứng.
4. Sau khi có checkpoint, chạy real smoke qua chính `ComfyUIImageProvider`, không gọi workflow khác với production adapter.

## 7. Test strategy

- Unit: submit/history/view, deterministic seed, scale dimensions, lỗi HTTP/connection/history/timeout.
- API: configured state và reject khi thiếu config.
- Pipeline: provider unavailable → one probe/fallback/manifest.
- Real smoke: ComfyUI MPS + checkpoint, tạo PNG synthetic prompt, không dùng dữ liệu thật.

