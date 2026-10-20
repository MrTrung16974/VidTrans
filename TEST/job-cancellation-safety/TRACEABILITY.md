# Traceability — job-cancellation-safety

| Yêu cầu | Thiết kế | Code | Test | Trạng thái |
|---|---|---|---|---|
| BR01_01 / AC01 | DESIGN §2 | `JobStore.request_cancel`, `cancel_job_endpoint` | TC01, TC02 | PASS |
| BR01_02 / AC02 | DESIGN §2 | `JobStore.request_cancel` | TC01, TC03 | PASS |
| BR01_03 | DESIGN §2 | `JobStore.request_cancel` | TC01 | PASS |
| BR01_04 | DESIGN §5 | `job_for_request` (không đổi) | TC16 | PASS |
| BR02_01 / AC04 | DESIGN §3 | `make_ocr_progress_callback`, `make_asr_progress_callback` | TC08 | PASS |
| BR02_02 / AC06 | DESIGN §3 | `synthesize_tts_segments` | TC10 | PASS |
| BR02_03 / AC05 | DESIGN §3 | `run_ffmpeg(job_id=)` | TC09 | PASS |
| BR02_04 / AC03 / AC08 | DESIGN §8 | handler `JobCancelled`, `JobScheduler._acknowledge_cancellation`, `finalize_cancellation` | TC06, TC07, TC13 | PASS |
| BR02_05 / AC07 | DESIGN §2, §8 | `JobStore.update`, `update_job` | TC11, TC12 | PASS |
| BR03_01 | DESIGN §8 | `JobStore.retry`, `delete(require_terminal=)`, endpoints | TC03, TC04, TC05 | PASS |
| BR03_02 / AC09 (UI) | DESIGN §4 | `jobActions` | TC18 | PASS (static) |
| BR03_03 | DESIGN §4 | `jobAction` toast | TC18 | PASS (static) |
| AC09 (publish) | DESIGN §8 | `publish_scheduled_tiktok`, `finish_cancelled_after_publish` | TC14, TC15 | PASS |
| Recovery | DESIGN §8 | `requeue_interrupted`, `recover_interrupted` | TC17 | PASS |
