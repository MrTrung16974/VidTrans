# Kế hoạch triển khai an toàn khi huỷ tiến trình xử lý video

- Feature: `job-cancellation-safety`
- Version: `0.1`
- Trạng thái: `APPROVED`
- Ngày: 2026-10-20
- Nguồn: yêu cầu người dùng ngày 2026-10-20 và `ANALYSIS.md` version 0.1.
- Approval record: version 0.1/phạm vi toàn bộ plan | người dùng | 2026-10-20 | “bắt đầu xử lý”.

## 1. Kết quả mong muốn

Thao tác Huỷ phản ánh đúng vòng đời thật: job đang chạy chuyển sang `cancelling`, pipeline dừng cooperative nhanh nhất có thể, worker xác nhận `cancelled`, sau đó mới cho Retry/Delete và release quota. Không tạo hai worker trên cùng job và không xóa file đang được sử dụng.

## 2. Kế hoạch

| Bước | Đầu vào | Việc làm | File đầu ra | Cách kiểm chứng | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| 1. BA | ANALYSIS 0.1 | Viết URD, state table, API behavior, AC/NFR; review giới hạn external publish. | `BA_URD/job-cancellation-safety/URD.md`, `REVIEW.md`, `OPEN_QUESTIONS.md` | Kiểm tra ID/luồng/state nhất quán. | Duyệt plan 0.1 | DONE |
| 2. Design | URD đã chốt | Thiết kế transaction state, cancellation token/checkpoint, quota timing, compatibility và test strategy. | `DEV/job-cancellation-safety/DESIGN.md` | Review concurrency, idempotency, authz, side effect ngoài hệ thống. | Bước 1 | DONE |
| 3. Store/API | Design | Đổi `processing -> cancelling`; queued/scheduled vẫn huỷ đồng bộ; chặn retry/delete; chỉ release quota khi terminal. | `backend/infrastructure/job_store.py`, `backend/main.py`, test store/API | Unit test state transition và 409 race. | Bước 2 | DONE |
| 4. Pipeline | Design | Truyền `job_id` tường minh cho subprocess; check cancel trong OCR/ASR/TTS và trước/sau integration dài; giữ late-update guard. | `backend/main.py`, `backend/pipeline/*` khi cần, unit tests | Test mock xác nhận `JobCancelled`, không bắt đầu work mới và terminate subprocess. | Bước 3 | DONE |
| 5. Scheduler/quota | Design | Bảo đảm worker ack terminal, release quota đúng lúc, không claim job cancelling. | scheduler/service/main và tests nếu cần | Test lifecycle/call counts với fake runner/governance. | Bước 3–4 | DONE |
| 6. UI | API contract | Hiển thị `Đang huỷ`; toast đúng; khóa Retry/Delete khi cancelling. | `backend/frontend/js/app.js`, static tests | Frontend static/unit assertions. | Bước 3 | DONE |
| 7. TEST | Code hoàn tất | Viết testcase và chạy targeted tests, sau đó regression suite phù hợp. | `TEST/job-cancellation-safety/TESTCASES.md`, `TEST_REPORT.md`, `TRACEABILITY.md` | Ghi lệnh/exit code/PASS-FAIL thực tế. | Bước 3–6 | DONE |
| 8. Handoff | Toàn bộ artifact | Ghi implementation, giới hạn, test evidence và hướng deploy/rollback. | `DEV/.../IMPLEMENTATION.md`, `BA_URD/.../HANDOFF.md` | Đối chiếu diff và traceability. | Bước 7 | DONE |

## 3. Thiết kế dự kiến cần review

1. `request_cancel`:
   - `queued`/`scheduled` → `cancelled`, đặt `finished_at`.
   - `processing` → `cancelling`, đặt `cancel_requested=true`, chưa đặt `finished_at`.
   - `cancelling`/terminal → idempotent, không làm mất dữ liệu.
2. Worker/pipeline bắt `JobCancelled`, cập nhật `cancelled`, cleanup an toàn và release quota.
3. `update` không cho late worker chuyển `cancelling` hoặc `cancelled` sang `completed`; chỉ đường xác nhận huỷ được phép chuyển `cancelling -> cancelled`.
4. Retry/Delete từ chối `cancelling`. Retry chỉ reset cờ huỷ sau khi trạng thái đã terminal.
5. `run_ffmpeg(..., job_id=...)` dùng tham số tường minh; thread-local chỉ là fallback compatibility.
6. OCR/ASR progress callback gọi `ensure_job_active` trước update. TTS kiểm tra giữa các pha và không submit toàn bộ cue một lần nếu điều đó làm hàng đợi tiếp tục sau huỷ.
7. Publish ngoài hệ thống: kiểm tra trước khi gọi; nếu cancel trong lúc request đang chạy thì chờ kết quả và ghi trạng thái reconciliation phù hợp, không khẳng định upload đã bị rollback.

## 4. Test strategy

- Store state matrix: queued, scheduled, processing, cancelling, completed, failed, cancelled.
- Idempotent cancel và late updates.
- API quyền sở hữu hiện hữu không bị suy giảm; tenant khác vẫn 404.
- Cancel→Retry và Cancel→Delete trong khi worker chưa ack đều 409.
- Callback OCR/ASR ném cancel với mock store.
- FFmpeg mock process nhận terminate và kill fallback.
- TTS không submit/start thêm cue sau cancel; lỗi từ worker được propagate.
- Quota release call count đúng cho synchronous và asynchronous cancellation.
- UI action visibility/toast/status label.
- Không gọi TikTok thật; publisher được fake để test trạng thái khi cancel đến trước/trong/sau publish.

## 5. Rủi ro và rollback

| Rủi ro | Kiểm soát |
|---|---|
| Client cũ chỉ biết `cancelled` | `cancelling` đã tồn tại trong UI/filter; JSON shape không đổi. |
| Operation thư viện không interrupt được | Cooperative checkpoint và timeout; không dùng kill Python thread không an toàn. |
| Deadlock trạng thái `cancelling` sau worker crash | Startup recovery phải chuyển/requeue có chủ đích; bổ sung regression test và runbook note. |
| Quota bị release hai lần | Dựa vào governance idempotency hiện hữu và test call/state; release chỉ tại terminal path. |
| Upload ngoài hệ thống có kết quả không chắc chắn | Không báo “đã huỷ hoàn toàn”; lưu trạng thái cần đối soát. |
| Regression checkpoint/retry | Targeted tests + suite backend phù hợp; không xóa checkpoint khi chỉ cancel. |

Rollback code: hoàn nguyên các file source/UI của feature; không có migration DB dự kiến. Artifact tài liệu giữ lại làm lịch sử. Không deploy trong phạm vi phiên này.

## 6. Definition of Done

- State machine và API đáp ứng AC01–AC09 đã được duyệt.
- Không thể Retry/Delete trước worker ack.
- Các stage kiểm tra cancel tại điểm kiểm soát hợp lý và FFmpeg có thể bị terminate.
- Test liên quan chạy PASS; mọi test chưa chạy được ghi rõ, không suy diễn SIT/UAT.
- URD, DESIGN, IMPLEMENTATION, TESTCASES, TEST_REPORT, TRACEABILITY và HANDOFF khớp code thực tế.

## 7. Tự review plan

- Scope: đủ backend, worker, pipeline, quota, UI và tests; không mở rộng sang scheduler phân tán.
- Compatibility: giữ endpoint và response shape, tận dụng trạng thái `cancelling` đã có.
- Security: giữ object-level authorization hiện hữu; không đọc/sửa cấu hình nhạy cảm.
- Concurrency: đã bao phủ hai race nguy hiểm nhất là Retry/Delete trước worker ack.
- External side effect: TikTok được tách thành giới hạn cần đối soát, không hứa rollback không khả thi.
- Testability: mọi integration ngoài hệ thống được mock/fake; không dùng credential hoặc dữ liệu thật.
- Blocker: không còn; người dùng đã duyệt plan 0.1 ngày 2026-10-20.

