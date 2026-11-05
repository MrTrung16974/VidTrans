# Kế hoạch — AI Photo Slideshow Director

- Feature: `ai-photo-slideshow-director`
- Phiên bản: 0.1
- Trạng thái: APPROVED
- Phạm vi: backend + renderer + web + app + tests; chưa triển khai ở giai đoạn plan.
- Nguồn: `ANALYSIS.md` 0.1 và source hiện tại.

## 1. Chiến lược triển khai

Chia thành hai mốc để giảm rủi ro:

- MVP — AI Director có kiểm soát: AI tạo plan dựng + gợi ý nhạc; renderer hỗ trợ per-slide timing/motion và transition cơ bản; người dùng review rồi áp dụng.
- Phase 2 — Beat-aware: phân tích BPM/onset của file nhạc thật, căn cut/crossfade theo beat, chấm điểm chất lượng ảnh và đề xuất loại ảnh trùng/kém.

Không đưa beat-sync vào MVP vì metadata thư viện hiện chỉ có title/category/duration, chưa có BPM hoặc waveform analysis.

## 2. Luồng kiến trúc MVP

1. Web/app gọi `POST /api/v1/jobs/{job_id}/photo-post/ai-plan`.
2. Server kiểm tra owner, entitlement, trạng thái `awaiting_input` và file ảnh còn tồn tại.
3. Planner lấy caption, ảnh đại diện đã giới hạn kích thước và danh sách nhạc đã sanitize.
4. LLM trả JSON theo schema; parser validate và chuẩn hóa thành `SlideshowPlan`.
5. Nếu model lỗi/timeout/plan không hợp lệ, heuristic planner tạo preset từ số ảnh + keyword mood.
6. Server trả plan cùng `plan_id`, `source`, `prompt_version`, lý do và music suggestions; không trừ quota.
7. Người dùng nghe thử, chỉnh duration/track, áp dụng rồi gọi endpoint tạo video hiện tại với `slideshow_plan_id`.
8. Server validate lại plan, snapshot vào `photo_choice`, reserve quota một lần và queue render.
9. Renderer build FFmpeg graph từ plan allowlist; lỗi plan không được chuyển thành shell/filter tùy ý.

## 3. Contract dự kiến

### Request tạo plan

```json
{
  "target_minutes": 1,
  "keep_original_order": false,
  "preferred_style": "auto"
}
```

### Response rút gọn

```json
{
  "plan_id": "opaque-id",
  "source": "gemini-or-heuristic",
  "style": "cinematic",
  "mood": "ấm áp, hoài niệm",
  "summary": "Mở bằng ảnh chân dung rõ nhất, tăng nhịp ở giữa và kết nhẹ.",
  "target_seconds": 60,
  "confidence": 0.82,
  "slides": [
    {
      "image_index": 2,
      "duration_seconds": 4.5,
      "motion": "slow_zoom_in",
      "transition": "crossfade",
      "transition_seconds": 0.4,
      "role": "hook"
    }
  ],
  "music_suggestions": [
    {
      "track_id": "existing-id",
      "reason": "Nhịp và cảm xúc phù hợp với mạch ảnh",
      "score": 0.91
    }
  ]
}
```

Server không nhận filter FFmpeg, path file, URL nhạc hoặc prompt tùy ý từ client.

## 4. Kế hoạch công việc

| Bước | Đầu vào | Việc làm | File đầu ra dự kiến | Cách kiểm chứng | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| 1. Chốt BA | Analysis 0.1 | Chốt Q01–Q03, schema, quyền chỉnh plan và behavior fallback | `URD.md`, `OPEN_QUESTIONS.md`, `REVIEW.md` | Review AC/BR và contract | Duyệt plan | DONE |
| 2. Thiết kế | URD | Thiết kế domain `SlideshowPlan`, cache/idempotency, API, validator, renderer graph và UI state | `DEV/ai-photo-slideshow-director/DESIGN.md` | Threat review + contract review + test strategy | Bước 1 | DONE |
| 3. Planner backend | Design | Xây planner AI + heuristic, reuse music suggestion, giới hạn ảnh/byte/timeout | Source + unit tests | JSON invalid, hallucinated ID/index, timeout, no-model | Bước 2 | DONE |
| 4. API contract | Planner | Thêm endpoint `ai-plan`, mở rộng `PhotoPostChoice`, cập nhật OpenAPI | `backend/main.py`, `contracts/openapi.json`, tests | API test đã viết; runtime integration chưa đủ dependency | Bước 3 | IMPLEMENTED / PARTIAL TEST |
| 5. Renderer MVP | Plan schema | Hỗ trợ per-slide order/duration/motion và cut/crossfade/fade; giữ fallback renderer cũ | `tiktok_slideshow.py`, tests | Command builder unit test | Bước 2 | DONE |
| 6. Web | API ổn định | UI `AI dựng tối ưu`, thẻ plan, lý do, nghe thử/chọn nhạc, apply/edit/retry | `web/index.html`, `web/js/app.js`, CSS | JS syntax + diff check; browser UAT chưa chạy | Bước 4 | IMPLEMENTED / UAT TODO |
| 7. App Flutter | API ổn định | Model/repository/UI tương đương web; dùng preview audio hiện có | Jobs repository/screen | `flutter analyze` | Bước 4 | IMPLEMENTED / WIDGET TEST TODO |
| 8. E2E/quality | Tất cả | Test dữ liệu tổng hợp; benchmark CPU/time; so sánh preset cũ với AI plan | `TEST/...` | Test report | Bước 3–7 | PARTIAL |
| 9. Rollout | Test report | Feature flag, metrics, canary, fallback/rollback runbook | `DEPLOY/.../MANIFEST.md`, `RUNBOOK.md` | Smoke + metrics + rollback rehearsal | Bước 8 | TODO |

## 5. Test strategy bắt buộc

- Planner unit:
  - model trả JSON đúng;
  - JSON hỏng, timeout, exception;
  - index ảnh âm/vượt biên/lặp;
  - motion/transition lạ;
  - duration tổng không khớp;
  - track ID bịa, bị xóa hoặc file mất;
  - không có model/không có nhạc/không có caption.
- Authorization/API:
  - owner thành công;
  - user khác 404;
  - job không `awaiting_input` trả 409;
  - request đồng thời/retry không gọi model hoặc reserve quota trùng.
- Renderer:
  - 1 ảnh, nhiều ảnh, ảnh lỗi, ảnh ngang/dọc;
  - từng motion/transition allowlist;
  - tổng duration sai số ≤ 1 frame;
  - plan lỗi không được chèn filter/path vào FFmpeg.
- UI web/app:
  - loading > retry > fallback;
  - nghe thử một bài tại một thời điểm;
  - apply plan rồi sửa nhạc/duration;
  - giữ thứ tự gốc;
  - rời màn hình dừng audio;
  - manual flow không hồi quy.
- Quality evaluation:
  - bộ dữ liệu tổng hợp ít nhất 6 nhóm: chân dung, du lịch, sản phẩm, đồ ăn, sự kiện, cảm xúc;
  - review mù preset cũ vs AI plan theo hook, nhịp, phù hợp nhạc, độ mượt;
  - không tuyên bố AI “tối ưu hơn” nếu chưa đạt ngưỡng UAT đã chốt.

## 6. Definition of Done

- Contract plan có version và validator allowlist.
- AI lỗi vẫn có heuristic plan và luồng thủ công.
- Web/app feature parity cho phân tích, nghe thử, chọn nhạc và áp dụng.
- Renderer tạo đúng video từ plan; không nhận expression/filter tự do.
- Unit/API/renderer/UI test đạt; benchmark và phần NOT_RUN được ghi rõ.
- Feature flag cho phép tắt AI Director mà không tắt luồng bài ảnh hiện tại.
- Có metrics: plan latency, AI/fallback rate, apply rate, render failure, user override rate.

## 7. Rollout đề xuất

- Stage 0: internal only, log metrics đã masking, không auto-apply.
- Stage 1: 10% user có feature flag, AI suggestion opt-in.
- Stage 2: 50% sau khi latency/error và UAT đạt ngưỡng.
- Stage 3: bật mặc định nhưng vẫn có nút dựng thủ công.
- Rollback: tắt flag endpoint/UI; `PhotoPostChoice` vẫn chấp nhận payload cũ và renderer cũ vẫn hoạt động.

## 8. Tự review plan

- Scope: đủ cả AI planning, music, renderer, web và app; beat-sync tách phase 2.
- Compatibility: endpoint/payload cũ tiếp tục dùng; AI plan là tùy chọn.
- Security/privacy: owner check, giới hạn ảnh/byte, không log raw content, validate toàn bộ output model.
- Testability: tách planner thuần, validator và renderer command builder để unit test.
- Cost/latency: cache/fingerprint, ảnh mẫu giới hạn, timeout và fallback.
- Rollback: feature flag và renderer cũ còn nguyên.

## 9. Điểm cần duyệt

- Duyệt MVP theo ba mặc định đề xuất:
  1. AI được đổi thứ tự ảnh nhưng có tùy chọn giữ thứ tự gốc.
  2. MVP không tự bỏ ảnh; chỉ cảnh báo ảnh trùng/kém.
  3. AI gợi ý độ dài, người dùng vẫn được chỉnh.
- Chưa coding cho đến khi plan 0.1 được duyệt.

## 10. Approval record

| Phiên bản/phạm vi | Người duyệt | Thời điểm | Nội dung xác nhận |
|---|---|---|---|
| 0.1 / MVP AI Director + music + renderer + web/app | Người dùng | 2026-11-05 | “oki bắt đầu”; chấp thuận ba mặc định tại mục 9 |

