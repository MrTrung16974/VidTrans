# Thiết kế — Huỷ an toàn tiến trình xử lý video

- Feature: `job-cancellation-safety`
- Version: `1.0`
- Nguồn: PLAN 0.1 APPROVED, URD 1.0.
- Câu hỏi chặn: không có.

## 1. Mapping

| Yêu cầu | Thành phần |
|---|---|
| BR01/BR03 | `JobStore.request_cancel/update`, cancel/retry/delete endpoints, `jobActions` UI |
| BR02_01 | `make_ocr_progress_callback`, `make_asr_progress_callback` |
| BR02_02 | `synthesize_tts_segments`, bounded submission/checkpoints |
| BR02_03 | `run_ffmpeg(args, job_id=...)` |
| BR02_04/AC08 | exception handlers trong `process_video`, `resume_process_video`, scheduled publish |
| BR02_05 | `JobStore.update` transition guard |

## 2. State và transaction

`request_cancel` dùng `BEGIN IMMEDIATE`, đọc status và payload trong cùng transaction. `processing` đi `cancelling`; `queued/scheduled` đi `cancelled`. Terminal/cancelling idempotent.

`JobStore.update` bảo vệ:

- từ `cancelled`: chỉ update có status `cancelled` được merge;
- từ `cancelling`: chỉ transition status `cancelled` được merge; update khác trả payload hiện tại;
- nhờ vậy late worker không thể hoàn thành/thất bại job đã nhận huỷ.

Không đổi schema; cột `cancel_requested` hiện hữu là nguồn kiểm tra nhanh, payload giữ contract API.

## 3. Pipeline cancellation

- `run_ffmpeg` nhận keyword `job_id`; nếu không truyền thì fallback thread-local để tương thích call cũ.
- Các helper chạy trong TTS thread truyền `job_id` tới FFmpeg và gọi `ensure_job_active` giữa network TTS, trim, adaptive render và fitting.
- OCR/ASR callback gọi `ensure_job_active` trước throttling/update để cancel không bị trì hoãn bởi logic giảm tần suất progress.
- Executor TTS vẫn bounded 5; task chưa bắt đầu sẽ thoát ngay tại checkpoint. Python future đang chạy không bị kill cưỡng bức.

## 4. API/UI/quota

- Cancel endpoint chỉ release quota nếu store trả terminal `cancelled`.
- Worker exception handler release quota sau khi ghi terminal.
- Retry/Delete đã chặn `cancelling` ở backend; UI chỉ hiển thị Retry/Delete cho terminal và không hiển thị nút Cancel khi đã cancelling.
- Toast dựa trên status response: `cancelling` → “Đang huỷ job…”, `cancelled` → “Đã huỷ job”.

## 5. Security và compatibility

- Giữ `job_for_request` trước mọi mutation; client không cung cấp owner/tenant.
- Không thêm config/secret/log payload.
- Endpoint và JSON shape không đổi. Client phải chấp nhận trạng thái `cancelling`, vốn đã có label/filter.

## 6. Test strategy

- Mở rộng `test_job_store.py` cho state matrix và late updates.
- Thêm unit test callback cancellation/FFmpeg với mock, ưu tiên không import provider hoặc gọi network thật.
- Static frontend assertions cho action/toast.
- Chạy targeted unittest; sau đó chạy suite backend nếu thời gian/phụ thuộc cho phép.

## 7. Tradeoff

Độ trễ dừng của OCR/ASR/TTS phụ thuộc callback/checkpoint của thư viện. Thiết kế ưu tiên nhất quán dữ liệu và dừng an toàn thay vì kill thread cưỡng bức có thể làm hỏng interpreter/GPU state.


## 8. Cập nhật 1.1 khi triển khai (2026-10-20)

Bổ sung sau khi rà code thực tế, không đổi phạm vi đã duyệt:

- `JobStore.retry`: đường duy nhất đưa job terminal về `queued`, kiểm tra trạng thái trong cùng transaction (`BEGIN IMMEDIATE`). Trước đây Retry dùng `update` nên bị guard `cancelled` bỏ qua âm thầm.
- `JobStore.delete(require_terminal=True)`: endpoint xóa row trước, file sau, để không xóa file khi worker còn dùng.
- `JobStore.finalize_cancellation`: chuyển `cancelling → cancelled` và trả cờ `changed` để release quota đúng một lần.
- `JobScheduler(on_cancelled=...)`: sau khi runner trả về hoặc ném lỗi, worker gọi `finalize_cancellation`; nếu job còn `cancelling` thì chốt `cancelled` và release quota. Không còn trường hợp kẹt `cancelling`.
- `update_job`: lỗi `failed` xảy ra sau khi job đã `cancelling` được ghi thành `cancelled`.
- Publish TikTok: kiểm tra huỷ trước khi gọi provider; nếu huỷ đến khi upload đã chạy và provider trả kết quả, job thành `cancelled`, giữ `tiktok_publish_*`, `step_detail` cảnh báo cần kiểm tra trên TikTok và **consume** quota (dịch vụ đã thực hiện). `request_cancel` ở bước `publishing-tiktok` cũng ghi cảnh báo này.
