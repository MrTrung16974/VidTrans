# Review — job-cancellation-safety

| RV ID | Artifact | Finding | Mức độ | Cách xử lý | Trạng thái |
|---|---|---|---|---|---|
| RV01 | URD 1.0 | Cần phân biệt accepted cancellation và completed cancellation. | Cao | Dùng `cancelling` rồi worker ack `cancelled`. | CLOSED |
| RV02 | URD 1.0 | Retry/Delete có thể phá file/state khi worker sống. | Cao | Chỉ cho phép sau terminal. | CLOSED |
| RV03 | URD 1.0 | External publish không rollback chắc chắn. | Cao | Ghi giới hạn; không hứa cancel in-flight. | DEFERRED/Q01 |
| RV04 | URD 1.0 | Cần giữ object-level authorization. | Cao | Không thay đổi `job_for_request`; TC16 + `test_api_tenant_isolation` PASS. | CLOSED |

