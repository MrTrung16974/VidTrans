# Implementation — job-cancellation-safety

- Version: 1.0
- Ngày: 2026-10-20
- Nguồn: PLAN 0.1 APPROVED, URD 1.0, DESIGN 1.0 + mục 8 (1.1).

## Thay đổi theo file

| File | Thay đổi |
|---|---|
| `backend/infrastructure/job_store.py` | `request_cancel` hai pha (`queued/scheduled → cancelled`, `processing → cancelling`), cảnh báo khi đang publish TikTok; guard `update` cho `cancelling/cancelled`; thêm `JobStateConflictError`, `TERMINAL_STATUSES`, `retry`, `finalize_cancellation`, `delete(require_terminal=)`; recovery khi khởi động chốt `cancelling → cancelled` thay vì chạy lại. |
| `backend/application/job_service.py` | Wrapper `retry`, `finalize_cancellation`, `delete(require_terminal=)` kèm phát sự kiện thay đổi. |
| `backend/application/job_scheduler.py` | `on_cancelled` callback; worker xác nhận huỷ sau khi runner kết thúc (kể cả lỗi). |
| `backend/main.py` | `run_ffmpeg(job_id=)`; checkpoint huỷ ở callback OCR/ASR và quanh FFmpeg trong thread TTS; `update_job` đổi `failed` muộn thành `cancelled`; cancel endpoint chỉ release quota khi `cancelled`; Retry/Delete dùng thao tác nguyên tử của store (409 khi chưa terminal); publish TikTok kiểm tra trước upload và xử lý huỷ in-flight qua `finish_cancelled_after_publish`; release quota cho job `cancelled` sau recovery; scheduler nhận `on_cancelled=TENANT_GOVERNANCE.release`. |
| `backend/frontend/js/app.js` | Ẩn nút Huỷ khi `cancelling`; toast “Đang dừng job an toàn…” khi API trả `cancelling`. Retry/Xóa vẫn chỉ hiện ở trạng thái terminal. |
| `backend/tests/*` | Xem `TEST/job-cancellation-safety/TESTCASES.md`. |

## Giới hạn đã biết

- Không kill cưỡng bức Python thread/GPU: OCR/ASR/TTS dừng ở callback/checkpoint kế tiếp.
- Upload TikTok đã gửi đi không thể thu hồi; hệ thống chỉ cảnh báo trung thực (Q01 DEFERRED).
- Startup release quota cho mọi job `cancelled` (idempotent, chỉ tác động bản ghi còn `reserved`).

## Rollback

Hoàn nguyên các file trên; không có migration DB. Không deploy trong phiên này.
