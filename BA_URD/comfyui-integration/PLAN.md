# Kế hoạch — Tích hợp ComfyUI với VidTrans

- Feature: `comfyui-integration`
- Version: `0.1`
- Trạng thái: `APPROVED`
- Nguồn yêu cầu: yêu cầu người dùng ngày 2026-10-24; bằng chứng trong `ANALYSIS.md`
- Approval record: v0.1/phạm vi local integration | Người dùng | 2026-10-24 | “oki bắt đầu”

## 1. Kết quả mong muốn

Hoàn tất đường chạy local từ UI/API VidTrans tới ComfyUI, sinh được ít nhất một ảnh thật và có bằng chứng test. Tái sử dụng adapter hiện có thay vì xây lại provider.

## 2. Definition of Done

- BA: yêu cầu, ranh giới mạng, lỗi/fallback và checkpoint compatibility được mô tả, các câu hỏi chặn được giải quyết.
- DEV: cấu hình/readiness cần thiết được hoàn thiện; không hardcode endpoint hay tên model; unit test chịu ảnh hưởng PASS.
- TEST: provider unit test, comic regression test và một smoke test thật có kết quả tách biệt.
- DEPLOY local: runbook ghi rõ native/Docker, start/stop, health check, model path và rollback; không tự deploy production.

## 3. Các bước thực hiện

| Bước | Đầu vào | Việc làm | File đầu ra | Cách kiểm chứng | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| 1. BA/URD | `ANALYSIS.md`, code hiện hữu | Chốt model và mode chạy; đặc tả UC/BR/AC/NFR | `BA_URD/comfyui-integration/URD.md`, `OPEN_QUESTIONS.md`, `REVIEW.md` | Self-review ID/luồng/AC | Người dùng duyệt plan và chọn checkpoint | DONE |
| 2. Design | URD đã đủ quyết định | Mapping cấu hình, readiness, provider, fallback và test strategy | `DEV/comfyui-integration/DESIGN.md` | Review compatibility và network boundary | Bước 1 | DONE |
| 3. DEV | Design | Chỉ sửa phần gap được xác nhận; giữ provider contract hiện hữu | Source/test tại vị trí repo; `IMPLEMENTATION.md` | Unit tests mục tiêu | Bước 2 | DONE — test-only |
| 4. TEST | Build local | Chạy provider/comic regression; smoke một prompt thật | `TEST/comfyui-integration/TESTCASES.md`, `TEST_REPORT.md`, `TRACEABILITY.md` | PASS/FAIL/NOT_RUN có evidence | Có checkpoint tương thích | DONE for provider; comic API NOT_RUN |
| 5. DEPLOY local | Test report | Viết runbook cấu hình native/Docker, health, start/stop, rollback | `DEPLOY/comfyui-integration/RUNBOOK.md`, `MANIFEST.md` | Dry-run command và health check | Bước 4 | DONE for local handoff |
| 6. Handoff | Toàn bộ artifact | Chốt việc đã làm, giới hạn và bước tiếp theo | `BA_URD/comfyui-integration/HANDOFF.md` | Đối chiếu git diff/test evidence | Bước 1-5 | DONE |

## 4. File code dự kiến chịu ảnh hưởng

- `backend/infrastructure/image_providers.py`
- `backend/tests/test_image_providers.py`
- Có thể: route options/comic test nếu readiness được đưa ra API.
- `docs/comfyui-image-generation.md`
- `deploy/comfyui-local.sh` nếu smoke test chứng minh start/readiness chưa đủ ổn định.

Không dự kiến đổi DB, SQL, BPMN/DMN hay OpenAPI public trong phạm vi hiện tại.

## 5. Test strategy

1. Unit: workflow dimensions/seed/config; HTTP error; timeout; history error; tải ảnh và resize.
2. Integration cô lập: options chỉ enable khi cấu hình hợp lệ; comic producer dùng provider và fallback đúng.
3. Real smoke: `/system_stats`, submit một prompt, poll completion, xác nhận PNG đọc được và đúng kích thước.
4. Regression: nhóm test image provider + comic API/video liên quan; không chạy test cần credential/dịch vụ thật ngoài ComfyUI được chủ động cấu hình.

## 6. Rủi ro, blocker và rollback

- Blocker: chưa có checkpoint, nên chưa thể hoàn thành real smoke.
- Không tự tải model lớn hoặc chấp nhận license thay người dùng.
- Rollback code: hoàn nguyên các file source thuộc feature; provider `placeholder` vẫn là đường chạy an toàn.
- Rollback runtime: dừng ComfyUI bằng `./deploy/comfyui-local.sh stop`; bỏ cấu hình `VIDTRANS_COMFYUI_*` khỏi runtime do người vận hành quản lý.

## 7. Self-review plan

- Scope: bám đúng comic image generation, không mở rộng sang video diffusion/custom nodes.
- Compatibility: giữ contract `ImageProvider` và job queue hiện hữu.
- Security: không đọc/ghi secret config; không public port 8188; không log toàn bộ payload.
- Testability: có unit, integration và real smoke tách biệt.
- Rollback: có fallback placeholder và stop runtime.
- Gap cần người dùng quyết định: checkpoint và mode chạy local đích.

