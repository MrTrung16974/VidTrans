# URD — Huỷ an toàn tiến trình xử lý video

- Feature: `job-cancellation-safety`
- Version: `1.0`
- Trạng thái: `Reviewed for implementation`
- Ngày: 2026-10-20
- Nguồn phê duyệt phạm vi: người dùng duyệt PLAN 0.1 ngày 2026-10-20.

## I. Thông tin chung

### 1. Mục đích

Đảm bảo thao tác Huỷ không chỉ đổi nhãn trên UI mà còn ngăn pipeline tiếp tục tạo công việc mới, không cho Retry/Xóa khi worker cũ còn sống, và chỉ xác nhận `cancelled` sau khi worker dừng.

### 2. Phạm vi

Trong phạm vi: job store/service, API cancel/retry/delete, worker pipeline OCR/ASR/TTS/FFmpeg, quota và UI tiến trình. Ngoài phạm vi: kill Python thread/GPU kernel không an toàn và rollback side effect đã được nền tảng xã hội nhận.

### 3. Thuật ngữ

| Thuật ngữ | Ý nghĩa |
|---|---|
| `cancelling` | Yêu cầu huỷ đã được ghi nhận nhưng worker chưa xác nhận dừng. |
| `cancelled` | Không còn worker hợp lệ chạy job; Retry/Delete được phép. |
| Cooperative cancellation | Stage tự kiểm tra cờ huỷ tại checkpoint an toàn và thoát bằng `JobCancelled`. |

### 4. Nguồn

| ID | Nguồn | Trạng thái | Yêu cầu |
|---|---|---|---|
| SRC_01 | `ANALYSIS.md` 0.1 | Đã đọc | UC_01–UC_03 |
| SRC_02 | `PLAN.md` 0.1 | APPROVED | Toàn bộ |
| SRC_03 | Source/test hiện hữu nêu trong ANALYSIS | Đã xác minh | State/API/pipeline |

## II. Tổng quan

### AS-IS

`processing -> cancelled` xảy ra ngay tại HTTP request, trong khi worker còn chạy; UI mở Retry/Delete và quota được release sớm.

### TO-BE

```text
queued/scheduled --cancel--> cancelled
processing       --cancel--> cancelling --worker ack--> cancelled
cancelling       --retry/delete--> HTTP 409
```

## III. Yêu cầu chi tiết

### UC_01 — Yêu cầu huỷ job

| Thuộc tính | Nội dung |
|---|---|
| Actor | Người dùng có quyền trên job |
| Trigger | `POST /api/v1/jobs/{job_id}/cancel` |
| Pre-condition | Job tồn tại trong phạm vi tenant/quyền hiện hữu |
| Post-condition | Job chờ chưa chạy kết thúc ngay; job đang chạy vào `cancelling` |

| Mã BR | Điều kiện | Quy tắc |
|---|---|---|
| BR01_01 | `queued` hoặc `scheduled` | Chuyển `cancelled`, đặt `finished_at`, release quota. |
| BR01_02 | `processing` | Chuyển `cancelling`, đặt `cancel_requested=true`, không đặt `finished_at`, chưa release quota. |
| BR01_03 | `cancelling` hoặc terminal | Request lặp idempotent, không làm mất dữ liệu. |
| BR01_04 | Sai owner/tenant | Giữ hành vi 404 hiện hữu. |

### UC_02 — Worker dừng và xác nhận

| Mã BR | Quy tắc |
|---|---|
| BR02_01 | OCR/ASR kiểm tra cờ huỷ tại callback tiến độ. |
| BR02_02 | TTS kiểm tra trước/sau operation dài và không bắt đầu cue mới sau huỷ. |
| BR02_03 | FFmpeg kiểm tra huỷ tối đa mỗi 0,5 giây; terminate, sau đó kill nếu quá timeout. |
| BR02_04 | Khi bắt `JobCancelled`, worker chuyển `cancelling -> cancelled`, đặt `finished_at`, release quota. |
| BR02_05 | Late progress/failure/completion không ghi đè `cancelling/cancelled`. |

### UC_03 — Thao tác sau huỷ

| Mã BR | Quy tắc |
|---|---|
| BR03_01 | Retry/Delete trong `cancelling` trả HTTP 409. |
| BR03_02 | Retry/Delete chỉ mở khi `cancelled` hoặc trạng thái terminal khác. |
| BR03_03 | UI hiển thị “Đang huỷ” và toast không tuyên bố “Đã huỷ” khi API trả `cancelling`. |

### State transition

| Từ | Trigger | Đến | Side effect |
|---|---|---|---|
| queued/scheduled | cancel | cancelled | finished_at + release quota |
| processing | cancel | cancelling | giữ quota, worker nhận cờ |
| cancelling | worker `JobCancelled` | cancelled | finished_at + release quota |
| cancelling | late update khác | cancelling | bỏ qua |
| terminal | cancel lặp | không đổi | idempotent |

### Acceptance criteria

| ID | Given/When/Then |
|---|---|
| AC01 | Given queued/scheduled, when cancel, then cancelled ngay và không claim. |
| AC02 | Given processing, when cancel, then response là cancelling và Retry/Delete trả 409. |
| AC03 | Given cancelling, when worker tới checkpoint, then thoát và chuyển cancelled. |
| AC04 | Given OCR/ASR callback sau cancel, then ném `JobCancelled`. |
| AC05 | Given FFmpeg đang chạy, when cancel, then terminate/kill và không commit output hoàn tất. |
| AC06 | Given TTS nhiều cue, when cancel, then không bắt đầu cue mới sau checkpoint. |
| AC07 | Given late completed/failed update, then không ghi đè cancelling/cancelled. |
| AC08 | Quota chỉ release khi job terminal cancelled. |
| AC09 | UI chỉ mở Retry/Delete khi terminal. |

### NFR

| ID | Yêu cầu | Cách kiểm tra |
|---|---|---|
| NFR_01 | Cancel idempotent và an toàn concurrency. | Unit test transaction/state matrix. |
| NFR_02 | Không log transcript, credential hoặc PII mới. | Code review. |
| NFR_03 | API shape không đổi; bổ sung hành vi trạng thái `cancelling` đã tồn tại. | Regression API/static test. |
| NFR_04 | Không kill Python thread cưỡng bức. | Code review. |

## IV. Phụ lục

- Không đổi DB schema, không có SQL migration.
- Không có Kafka/Camunda/DMN.
- External publish không thể đảm bảo rollback sau khi request đã rời hệ thống; pipeline sẽ kiểm tra huỷ trước khi gọi provider, còn tình huống in-flight cần reconciliation riêng nếu mở rộng sau này.

