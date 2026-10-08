# Kế hoạch — Dịch đa ngôn ngữ

- Feature: `multilingual-translation`
- Version: `0.1`
- Trạng thái: `IMPLEMENTED_LOCAL`
- Phạm vi: BA → DEV → TEST; chỉ chuẩn bị runbook deploy, không tự triển khai VPS khi chưa có kênh truy cập hợp lệ.
- Nguồn: `ANALYSIS.md` cùng thư mục và mã nguồn hiện tại tại commit được kiểm tra khi bắt đầu DEV.

## Kết quả mong muốn

Hệ thống hỗ trợ chọn nguồn `auto/vi/en/zh-CN/ja/ko/th/id/es`, chọn đích trong 8 ngôn ngữ cụ thể, giữ cấu hình xuyên suốt vòng đời job, dịch bằng provider động, xuất phụ đề đa script và lồng tiếng đúng locale.

## Definition of Done

- BA: URD có use case, business rule, acceptance criteria, NFR và traceability.
- DEV: catalog ngôn ngữ, domain/API/ASR/translation/TTS/UI được cập nhật; job cũ tương thích.
- TEST: unit test và testcase regression chạy có bằng chứng; test render font tối thiểu cho CJK, Latin, Hàn và Thái.
- DEPLOY: manifest/runbook nêu precheck, rollout, smoke test và rollback; không có SQL/Camunda nếu không phát sinh thay đổi tương ứng.

## Kế hoạch thực hiện

| Bước | Đầu vào | Việc làm | File đầu ra | Cách kiểm chứng | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| 1. Chốt BA | ANALYSIS 0.1 | Viết UC/BR/AC/NFR, chốt catalog và giới hạn OCR | `BA_URD/multilingual-translation/URD.md`, `OPEN_QUESTIONS.md`, `REVIEW.md` | Review consistency và acceptance criteria | Người dùng duyệt plan/phạm vi | Hoàn tất |
| 2. Thiết kế | URD đã chốt | Thiết kế LanguageProfile, contract translator/ASR/TTS, compatibility job cũ | `DEV/multilingual-translation/DESIGN.md` | Mapping UC/BR → module/test | Bước 1 | Hoàn tất |
| 3. Domain/API | DESIGN | Thêm source/target vào model, form, job config, retry/resume | source hiện hữu + unit/API tests | Request hợp lệ/không hợp lệ và retry legacy | Bước 2 | Hoàn tất |
| 4. ASR | LanguageProfile | Tổng quát hóa `transcribe_chinese`, hỗ trợ explicit/auto, trả detected language | `pipeline/asr.py` + tests | Mock cả faster/openai Whisper | Bước 3 | Hoàn tất |
| 5. Translation | contract mới | Tổng quát provider params, validation theo language, metadata động, giữ fallback/backoff | infrastructure/pipeline + tests | Google 429 → MyMemory; script đích hợp lệ | Bước 3 | Hoàn tất |
| 6. TTS/render | voice catalog | Voice nam/nữ theo target, gTTS locale động, font coverage | `main.py`/module mới + tests | Mock Edge/gTTS; render sample scripts | Bước 3, 5 | Hoàn tất |
| 7. UI | API contract | Thêm select nguồn/đích, thay copy “Trung/Việt”, ràng buộc OCR/TTS | frontend + JS tests | FormData và trạng thái UI | Bước 3 | Hoàn tất |
| 8. Regression | code hoàn chỉnh | Chạy test chọn lọc rồi suite an toàn | `TESTCASES.md`, `TEST_REPORT.md`, `TRACEABILITY.md` | PASS/FAIL theo bằng chứng thực thi | Bước 3–7 | Hoàn tất có giới hạn suite ghi trong report |
| 9. Chuẩn bị rollout | kết quả test | Viết manifest và runbook tương thích job cũ | `deploy/multilingual-translation/MANIFEST.md`, `RUNBOOK.md` | Review rollback và smoke test | Bước 8 | Hoàn tất |

## Thứ tự lát triển khai

1. Catalog + domain + legacy defaults.
2. ASR source language.
3. Translation target language và validation.
4. TTS voice mapping.
5. API persistence/retry.
6. UI và copy động.
7. Artifact/TikTok metadata, regression và runbook.

Mỗi lát phải có unit test trước khi chuyển lát tiếp theo. Không chạy test có thể gọi dịch vụ thật hoặc tải model; các provider/Whisper/TTS được mock.

## Rủi ro và xử lý

- Mã locale không đồng nhất: quản lý qua một catalog có adapter theo provider.
- Legacy job: đọc thiếu field bằng default `zh-CN/vi`; job mới luôn ghi rõ hai field.
- OCR: API chặn `burned` khi source khác `zh-CN`; `auto` chuyển sang speech và ghi cảnh báo.
- TTS thiếu voice: validate trước enqueue với mode 2/3 và trả lỗi rõ.
- Provider quota: giữ circuit cooldown/backoff; không retry từng cue sau khi đã xác định rate limit toàn provider.
- Font: chọn Noto family theo script và có test sinh ASS/SRT/render ngắn.

## Tự review plan

- Scope: bao phủ toàn bộ đường đi create → worker → retry → artifact → UI.
- Compatibility: có default rõ cho job cũ; không yêu cầu migration DB khi chưa thấy schema cần đổi.
- Security: không đọc/sửa secret; proxy/API key tiếp tục qua cấu hình môi trường.
- Testability: provider, ASR và TTS đều có boundary để mock.
- Rollback: thay đổi additive ở job config; code cũ bỏ qua field mới, code mới đọc được job cũ.
- Gap cần duyệt: danh sách 8 ngôn ngữ và giới hạn OCR tiếng Trung.

## Approval record

| Version/phạm vi | Người duyệt | Thời điểm | Nội dung xác nhận |
|---|---|---|---|
| 0.1 — 8 ngôn ngữ, OCR Trung | Người dùng | 2026-10-08 | “oki bắt đầu đi” |
