# URD — Tích hợp ComfyUI local với VidTrans

- Feature: `comfyui-integration`
- Hệ thống: VidTrans
- Version: `0.1`
- Trạng thái: `Draft — phạm vi plan đã được duyệt, checkpoint còn OPEN`
- Ngày: 2026-10-24

## I. THÔNG TIN CHUNG

### 1. Mục đích

Cho phép pipeline **Truyện → Video** dùng ComfyUI chạy local để sinh ảnh cảnh mà không cần dịch vụ ảnh trả phí, đồng thời giữ job có thể hoàn thành bằng ảnh minh họa tạm khi dependency lỗi.

### 2. Phạm vi

Trong phạm vi: comic image provider, UI options, cấu hình native/Docker, local runtime, fallback, unit/regression test và real smoke khi có checkpoint. Ngoài phạm vi: custom node, IP-Adapter/ControlNet/LoRA, video diffusion, public Internet exposure và production deployment.

### 3. Thuật ngữ

| Thuật ngữ | Ý nghĩa |
|---|---|
| ComfyUI | Dịch vụ local thực thi workflow sinh ảnh qua HTTP API |
| Checkpoint | File model được ComfyUI tải bằng `CheckpointLoaderSimple` |
| Provider | Adapter `ImageProvider` của VidTrans |
| Fallback | Ảnh minh họa tạm được tạo local khi provider AI lỗi |

### 4. Nguồn tham chiếu

| Source ID | Nguồn | Trạng thái | Yêu cầu |
|---|---|---|---|
| SRC_01 | `BA_URD/comfyui-integration/ANALYSIS.md` | Đã đọc | UC_01, UC_02 |
| SRC_02 | `backend/infrastructure/image_providers.py` | Đã đọc | UC_01, UC_02 |
| SRC_03 | `backend/application/comic_routes.py` | Đã đọc | UC_01 |
| SRC_04 | `backend/application/comic_service.py` | Đã đọc | UC_01 |
| SRC_05 | `backend/pipeline/comic_video.py` | Đã đọc | UC_02 |
| SRC_06 | `docs/comfyui-image-generation.md` | Đã đọc | NFR_01 |

## II. TỔNG QUAN HỆ THỐNG

### Actor

| Actor | Trách nhiệm |
|---|---|
| Người dùng VidTrans | Chọn ComfyUI và tạo/preview comic |
| VidTrans API/worker | Xác thực lựa chọn, queue job, gọi provider và quản lý artifact |
| ComfyUI local | Queue workflow, thực thi model, trả lịch sử và ảnh |

### AS-IS/TO-BE

AS-IS: code integration đã tồn tại; runtime ComfyUI đã cài nhưng chưa có checkpoint và chưa có end-to-end evidence. TO-BE: runtime có checkpoint đã duyệt, backend có URL/tên checkpoint, UI cho chọn provider và ít nhất một preview thật sinh thành công.

## III. YÊU CẦU CHI TIẾT

### UC_01 — Chọn ComfyUI để sinh ảnh truyện

| Thuộc tính | Nội dung |
|---|---|
| Actor | Người dùng VidTrans |
| Trigger | Mở form Truyện → Video và chọn provider |
| Pre-condition | Đã đăng nhập; backend có URL và checkpoint; quyền gói cho phép provider |
| Post-condition | Comic job lưu `image_provider=comfyui` và được queue |

| Bước | Hành động | Kết quả | BR |
|---|---|---|---|
| MF01_01 | UI gọi comic options | Nhận danh sách provider và trạng thái cấu hình | BR01_01 |
| MF01_02 | Người dùng chọn ComfyUI và tạo project | API validate settings/quyền/config | BR01_02 |
| MF01_03 | API tạo job | Worker nhận settings đã lưu | BR01_03 |

- EF01_01: thiếu URL hoặc checkpoint → API trả 400 “Chưa cấu hình”, không tạo job.
- EF01_02: tài khoản không có entitlement → API trả 403, không tạo job.

| Mã BR | Quy tắc | Nguồn | Trạng thái |
|---|---|---|---|
| BR01_01 | Provider chỉ selectable khi cấu hình tối thiểu có URL và checkpoint | SRC_02/SRC_03 | CONFIRMED |
| BR01_02 | API không tin giá trị provider từ client; Pydantic giới hạn enum và kiểm tra status server-side | SRC_03 | CONFIRMED |
| BR01_03 | Tác vụ nặng chạy trong worker, không trong request-response | SRC_04 | CONFIRMED |

| AC | Given | When | Then |
|---|---|---|---|
| AC01_01 | URL và checkpoint có mặt | Gọi comic options | ComfyUI trả `configured=true` |
| AC01_02 | Thiếu một trong hai cấu hình | Tạo project với ComfyUI | API trả 400 và không queue job |

### UC_02 — Sinh ảnh qua ComfyUI và xử lý lỗi

| Thuộc tính | Nội dung |
|---|---|
| Actor | VidTrans worker/ComfyUI |
| Trigger | Worker cần tạo ảnh cho cảnh |
| Pre-condition | Comic job hợp lệ, ComfyUI truy cập được và checkpoint tương thích |
| Post-condition | PNG đúng kích thước được lưu, hoặc fallback có cảnh báo |

| Bước | Hành động | Kết quả | BR |
|---|---|---|---|
| MF02_01 | Provider dựng workflow core-node | Workflow chứa checkpoint, CLIP, latent, sampler, VAE, SaveImage | BR02_01 |
| MF02_02 | POST `/prompt` | Nhận `prompt_id` | BR02_02 |
| MF02_03 | Poll `/history/{prompt_id}` có deadline | Nhận metadata ảnh hoặc lỗi | BR02_03 |
| MF02_04 | GET `/view` | Ảnh được crop/upscale và lưu atomic PNG | BR02_04 |

- EF02_01: không kết nối/timeout/model lỗi → ghi lý do giới hạn, dùng placeholder theo cơ chế hiện hữu.
- EF02_02: API trả workflow error → không retry vô hạn; fallback giữ job tiếp tục.

| Mã BR | Quy tắc | Nguồn | Trạng thái |
|---|---|---|---|
| BR02_01 | Workflow mặc định chỉ dùng node core, checkpoint phải tương thích | SRC_02 | CONFIRMED |
| BR02_02 | Seed 0 được đổi thành seed deterministic từ prompt | SRC_02 | CONFIRMED |
| BR02_03 | Poll có timeout hữu hạn; dependency unavailable không được fan-out lỗi cho mọi worker | SRC_02/SRC_05 | CONFIRMED |
| BR02_04 | Ảnh đầu ra đúng kích thước request và ghi atomic | SRC_02 | CONFIRMED |

| AC | Given | When | Then |
|---|---|---|---|
| AC02_01 | ComfyUI/model hoạt động | Sinh preview | Tạo PNG thật đúng kích thước |
| AC02_02 | ComfyUI không kết nối được | Sinh preview/video | Job dùng placeholder và manifest có `image_fallback` |
| AC02_03 | Cùng prompt và seed 0 | Dựng workflow nhiều lần | Seed workflow giống nhau |

### Dữ liệu và trạng thái

Không đổi DB. `image_provider` nằm trong settings/resume request của comic job hiện hữu. Artifact ảnh nằm trong comic project và được cache theo provider cache key cùng inputs.

### Yêu cầu phi chức năng

| NFR | Yêu cầu | Cách kiểm tra | Trạng thái |
|---|---|---|---|
| NFR_01 | Không public cổng 8188 ra Internet | Review compose/runbook | CONFIRMED |
| NFR_02 | Không hardcode endpoint/tên model trong source | Review cấu hình | CONFIRMED |
| NFR_03 | Deadline hữu hạn và fallback khi dependency lỗi | Unit/regression test | CONFIRMED |
| NFR_04 | Không log secret hoặc toàn bộ payload không cần thiết | Code review | CONFIRMED |
| NFR_05 | MPS được dùng trên Apple Silicon khi khả dụng | Real smoke `/system_stats` | CONFIRMED |

## IV. PHỤ LỤC TÍCH HỢP

- ComfyUI API: `POST /prompt`, `GET /history/{prompt_id}`, `GET /view`, health evidence qua `GET /system_stats`.
- Auth: ComfyUI local không có auth trong phạm vi; boundary bảo vệ là host/LAN/firewall, không public port.
- Timeout: provider tổng mặc định 300 giây; mỗi HTTP call bị giới hạn nhỏ hơn.
- Retry: polling tới deadline; không resubmit cùng workflow tự động trong provider.
- Cấu hình: xem `DEV/comfyui-integration/DESIGN.md`.
- DB/SQL/Camunda: N/A, không có thay đổi.

