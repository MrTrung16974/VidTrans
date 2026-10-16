# Traceability — Tối ưu pipeline media

| UC/BR/AC/NFR | Nguồn | URD version | Design/code | Unit test | TC | Deploy artifact hoặc N/A | Trạng thái/gap |
|---|---|---|---|---|---|---|---|
| UC_01/BR01_01-03/AC01_01-03 | SRC_01, SRC_04 | 0.1 | `pipeline/stage_metrics.py`, `main.py::measure_job_stage` | `test_stage_metrics.py` | TC_001–003 | RUNBOOK pending | PASS unit; runtime benchmark pending |
| UC_02/BR02_01-04/AC02_01-03/NFR_06 | SRC_01, SRC_03 | 0.1 | `infrastructure/{artifact_io,stage_checkpoint}.py`, `pipeline/checkpoint_payloads.py`, `main.py::process_video` | `test_artifact_io.py`, `test_stage_checkpoint.py`, `test_checkpoint_payloads.py`, manual SIT harnesses | TC_006–007, TC_013–025 | RUNBOOK pending | Kill sau TTS/restart PASS; media duration FAIL DF_001; các ranh giới kill khác NOT_RUN |
| UC_03/BR03_04/AC03_01 | SRC_01, SRC_04 | 0.1 | `pipeline/parallel.py`, `main.py::synthesize_tts_segments` | `test_parallel.py` | TC_004–005 | RUNBOOK pending | PASS unit; worker error được propagate |
| UC_04/BR04_01/AC04_01 | SRC_01, SRC_04 | 0.1 | `infrastructure/media_probe.py`, `main.py::process_video` | `test_media_probe.py` | TC_008–010 | RUNBOOK pending | PASS unit + probe video local |
| NFR_01-04 | SRC_01, SRC_07 | 0.1 | Instrumentation nền | N/A | TC_012 | N/A đến khi có baseline | BLOCKED bởi Q04/Q05 |
| NFR_05 | SRC_01 | 0.1 | Metric schema chỉ có stage/status/duration/RTF/time | `test_stage_metrics.py` | TC_001–003 | RUNBOOK pending | PASS review/unit |
| NFR_07 | SRC_02, SRC_04 | 0.1 | Thay đổi additive, helper cũ giữ lại | 137 selected tests | TC_011 | RUNBOOK pending | PASS chọn lọc; full integration NOT_RUN |
