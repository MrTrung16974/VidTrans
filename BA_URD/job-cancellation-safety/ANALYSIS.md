# Phân tích an toàn khi huỷ tiến trình xử lý video

- Feature: `job-cancellation-safety`
- Version: `0.1`
- Trạng thái: `DRAFT`
- Ngày phân tích: 2026-10-20
- Nguồn yêu cầu: Người dùng yêu cầu tối ưu thao tác huỷ để tiến trình không tiếp tục chạy sau khi bấm Huỷ.

## 1. Vấn đề và mục tiêu

### AS-IS đã xác minh

1. `JobStore.request_cancel()` chuyển thẳng job đang chạy sang `cancelled`, đặt `finished_at` và `cancel_requested=true` trước khi worker thực sự thoát.
2. UI coi `cancelled` là trạng thái kết thúc nên mở ngay thao tác Retry và Xóa.
3. Retry đặt lại `cancel_requested=0`; worker cũ có thể tiếp tục, đồng thời job có thể được worker khác claim lại.
4. Delete có thể xóa source/work directory khi worker cũ còn sử dụng.
5. FFmpeg ở thread chính kiểm tra huỷ định kỳ, nhưng OCR/ASR callback chỉ cập nhật tiến độ. TTS chạy thread con và chỉ kiểm tra trước mỗi cue; context dùng để huỷ FFmpeg không được truyền sang thread TTS.
6. Publish TikTok đang chạy không có callback huỷ; trạng thái local có thể là `cancelled` trong khi side effect bên ngoài vẫn hoàn thành.
7. Quota được release tại HTTP endpoint trước khi worker xác nhận dừng.

### Mục tiêu đo được

- Job đang `processing` hoặc đang thực hiện publish khi nhận huỷ phải chuyển sang `cancelling`, không phải `cancelled`.
- Chỉ worker sở hữu job được chuyển `cancelling -> cancelled` sau khi pipeline đã thoát khỏi công việc có thể huỷ.
- Retry/Delete bị từ chối trong `cancelling`; không thể xóa cờ huỷ khi worker cũ còn sống.
- OCR và ASR kiểm tra huỷ tại callback tiến độ; TTS kiểm tra trước/sau các operation dài và FFmpeg trong thread TTS nhận đúng `job_id`.
- Quota chỉ được release khi queued/scheduled được huỷ đồng bộ hoặc khi worker đang chạy đã xác nhận kết thúc.
- Không ghi đè `cancelled` bằng cập nhật đến muộn.
- Có unit/integration test cô lập cho state transition và các race Retry/Delete.

## 2. Actor và hệ thống ảnh hưởng

| Actor/hệ thống | Vai trò |
|---|---|
| Người dùng đã đăng nhập | Yêu cầu huỷ job thuộc quyền truy cập hiện tại. |
| Job API | Xác thực quyền, gửi yêu cầu huỷ và trả trạng thái thực. |
| JobStore/JobService | Quản lý state transition và cờ huỷ bền vững. |
| JobScheduler/worker | Chạy pipeline cho tới khi nhận tín hiệu huỷ và xác nhận kết thúc. |
| OCR/ASR/TTS/FFmpeg | Các stage cần cooperative cancellation. |
| TenantGovernance | Reserve/release/consume quota đúng vòng đời. |
| Frontend tiến trình | Hiển thị `Đang huỷ`, khóa thao tác xung đột. |
| TikTok publisher | Side effect ngoài hệ thống, không phải operation có thể rollback chắc chắn. |

## 3. TO-BE

```text
queued/scheduled --Cancel--> cancelled (không có worker chạy)
processing       --Cancel--> cancelling --worker stopped--> cancelled
processing       --complete wins transaction--> completed
cancelling       --Retry/Delete--> 409
cancelled        --Retry/Delete--> allowed
```

- `request_cancel` phải quyết định trạng thái theo trạng thái hiện tại trong cùng transaction.
- Mọi điểm tiến độ dài gọi `ensure_job_active(job_id)`; khi thấy cờ huỷ thì ném `JobCancelled`.
- FFmpeg nhận `job_id` tường minh thay vì chỉ dựa vào thread-local, đặc biệt trong TTS executor.
- Worker cleanup/handler cuối cùng ghi `cancelled`, `finished_at` và release quota.
- API trả thông điệp `Đang huỷ job`; UI không tuyên bố đã huỷ xong khi trạng thái còn `cancelling`.

## 4. Phạm vi

### Trong phạm vi

- State machine huỷ của job video và job publish TikTok dùng chung bảng jobs.
- Backend API/store/service/pipeline cancellation points.
- UI trạng thái và điều kiện Retry/Delete.
- Unit/integration/regression tests an toàn, không gọi provider thật.
- Tài liệu BA/DEV/TEST và traceability.

### Ngoài phạm vi

- Kill cưỡng bức Python thread hoặc GPU kernel đang chạy; Python không hỗ trợ dừng thread an toàn.
- Cam kết rollback một video đã được nền tảng xã hội chấp nhận/upload; cần mô hình reconciliation riêng.
- Thay scheduler local bằng process queue phân tán.
- Deploy production, SQL migration hoặc Camunda; schema hiện có đã có `cancel_requested` và trạng thái nằm trong payload/text.

## 5. Acceptance criteria ban đầu

| ID | Tiêu chí |
|---|---|
| AC01 | Cancel job `queued/scheduled` kết thúc ngay ở `cancelled` và không được claim. |
| AC02 | Cancel job `processing` trả `cancelling`; Retry/Delete trả 409 cho tới khi worker xác nhận `cancelled`. |
| AC03 | Callback OCR/ASR phát hiện cờ huỷ ở lần callback kế tiếp và không tiếp tục toàn bộ stage. |
| AC04 | TTS không khởi tạo cue mới sau huỷ; FFmpeg TTS đang chạy nhận huỷ qua `job_id` tường minh. |
| AC05 | Render FFmpeg bị terminate, fallback kill sau timeout, và job kết thúc `cancelled`. |
| AC06 | Quota không release tại thời điểm chuyển `processing -> cancelling`; release đúng một lần khi worker kết thúc. |
| AC07 | Cập nhật muộn không thể chuyển job `cancelling/cancelled` thành `completed`. |
| AC08 | UI hiển thị `Đang huỷ`; không hiện Retry/Xóa cho tới `cancelled`. |
| AC09 | Huỷ trong lúc publish hiển thị cảnh báo trạng thái chưa chắc chắn; không báo sai rằng side effect đã được rollback. |

## 6. Rủi ro, giả định và câu hỏi

| Loại | ID | Nội dung | Xử lý đề xuất |
|---|---|---|---|
| CONFIRMED | R01 | Retry/Delete hiện có race với worker cũ. | Chặn theo `cancelling`, chỉ terminal sau worker ack. |
| CONFIRMED | R02 | Thread TTS không kế thừa thread-local `job_id`. | Truyền `job_id` tường minh vào wrapper FFmpeg. |
| CONFIRMED | R03 | Publish ngoài hệ thống không rollback chắc chắn. | Cooperative check trước upload; giữ trạng thái reconciliation/chi tiết cảnh báo nếu cancel xảy ra trong upload. |
| ASSUMPTION | A01 | Thay contract trạng thái từ `cancelled` tức thời sang `cancelling` được chấp nhận. | UI hiện đã có label/filter `cancelling`; giữ response JSON tương thích. |
| ASSUMPTION | A02 | Độ trễ dừng phụ thuộc checkpoint kế tiếp là chấp nhận được nếu operation thư viện không hỗ trợ cancel. | Bổ sung timeout/callback tại các điểm có thể kiểm soát. |
| OPEN | Q01 | Có yêu cầu huỷ upload TikTok qua API nền tảng hay chỉ cần tránh báo sai? | Không chặn hardening pipeline; ghi rõ giới hạn trong UI/URD. |

## 7. Nguồn bằng chứng

| Source ID | Vị trí | Nội dung |
|---|---|---|
| SRC_01 | `backend/infrastructure/job_store.py::request_cancel` | Chuyển thẳng sang `cancelled`. |
| SRC_02 | `backend/main.py::cancel_job_endpoint`, `retry_job_endpoint`, `delete_job_endpoint` | Release quota tức thời và cho phép Retry/Delete terminal. |
| SRC_03 | `backend/main.py::run_ffmpeg`, callbacks OCR/ASR, `synthesize_tts_segments` | Mức độ cooperative cancellation không đồng nhất. |
| SRC_04 | `backend/application/job_scheduler.py::_worker` | Worker giữ slot cho tới khi runner trả về. |
| SRC_05 | `backend/frontend/js/app.js::jobActions` | `cancelled` mở Retry/Delete ngay. |
| SRC_06 | `backend/tests/test_job_store.py` | Test hiện chỉ xác nhận trạng thái DB và late update, chưa kiểm tra worker thật/race. |

