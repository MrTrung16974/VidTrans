# Testcases — job-cancellation-safety

| TC ID | AC | Test | Mô tả |
|---|---|---|---|
| TC01 | AC01 | `test_job_store::test_cancel_state_matrix` | Ma trận 7 trạng thái; queued/scheduled → cancelled, processing → cancelling, terminal idempotent, không claim được. |
| TC02 | AC01/AC08 | `test_job_cancellation::test_cancel_queued_job_releases_quota_immediately` | Huỷ job chờ release quota ngay. |
| TC03 | AC02/AC08 | `test_job_cancellation::test_cancel_running_job_keeps_quota_and_blocks_retry_and_delete` | Job đang chạy → cancelling, không release quota, Retry/Delete 409, file nguồn còn. |
| TC04 | AC02 | `test_job_store::test_retry_and_delete_are_refused_until_worker_acknowledges` | Store từ chối retry/delete khi cancelling; finalize đúng một lần; sau đó retry và claim lại được. |
| TC05 | AC02 | `test_job_store::test_concurrent_retries_requeue_a_cancelled_job_once` | 4 retry đồng thời chỉ 1 thành công. |
| TC06 | AC03 | `test_job_scheduler::test_worker_acknowledges_cancelling_job_after_runner_returns` | Runner lỗi khi cancelling → worker chốt cancelled và release quota. |
| TC07 | AC03/AC08 | `test_job_scheduler::test_completed_job_is_not_released_by_acknowledgement` | Job không huỷ không bị release. |
| TC08 | AC04 | `test_job_cancellation::test_asr_progress_*`, `test_ocr_progress_*` | Callback OCR/ASR ném `JobCancelled`, không update tiến độ. |
| TC09 | AC05 | `test_job_cancellation::test_ffmpeg_uses_explicit_job_id_and_terminates_on_cancel` | FFmpeg dùng `job_id` tường minh và terminate. |
| TC10 | AC06 | `test_job_cancellation::test_tts_does_not_start_new_cue_after_cancel` | TTS không gọi edge-tts/FFmpeg sau huỷ. |
| TC11 | AC07 | `test_job_store::test_cancelled_job_ignores_late_worker_updates`, `test_job_cancellation::test_late_completion_while_cancelling_is_rejected` | Completed muộn không ghi đè. |
| TC12 | AC07 | `test_job_cancellation::test_late_failure_while_cancelling_finishes_as_cancelled` | Failed muộn thành cancelled, không kẹt. |
| TC13 | AC03 | `test_job_cancellation::test_worker_ack_then_retry_requeues_without_cancel_flag` | Sau ack, Retry đưa về queued và xóa cờ huỷ. |
| TC14 | AC09 | `test_job_cancellation::test_scheduled_publish_cancelled_before_upload_does_not_call_tiktok` | Huỷ trước upload không gọi provider. |
| TC15 | AC09 | `test_job_cancellation::test_cancel_during_scheduled_upload_keeps_result_and_warns`, `test_job_store::test_cancel_during_tiktok_upload_warns_that_it_cannot_be_recalled` | Huỷ khi đang upload: giữ kết quả, cảnh báo, consume quota. |
| TC16 | BR01_04 | `test_job_cancellation::test_cancel_by_other_tenant_is_not_found`, `test_api_tenant_isolation` | Tenant khác nhận 404, job không đổi. |
| TC17 | Recovery | `test_job_store::test_requeue_finalizes_cancelling_job_instead_of_running_it_again` | Restart không chạy lại job đang huỷ. |
| TC18 | AC09 (UI) | `test_frontend_static` | Toast theo `data.status`; nút Huỷ ẩn khi cancelling. |
