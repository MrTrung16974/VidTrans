# Review — Tối ưu pipeline media

| RV ID | Artifact/version/vị trí | Finding | Mức độ | Cách xử lý | Trạng thái | Bằng chứng kiểm tra lại |
|---|---|---|---|---|---|---|
| RV01 | URD 0.1/NFR_01-02 | Chưa có corpus và baseline nên ngưỡng chất lượng/hiệu năng chưa thể coi là approved | High | Giữ OPEN, không tuyên bố đạt trước Phase 0 | OPEN | `OPEN_QUESTIONS.md` Q04 |
| RV02 | URD 0.1/UC_02 | Cross-job cache có rủi ro privacy và retention | High | Lát đầu per-job; chờ Q03 trước khi reuse chéo job | MITIGATED | BR02_03 |
| RV03 | URD 0.1/UC_03 | TTS futures hiện có thể che lỗi worker | Critical | Sửa propagation trong Lát A và thêm failure test | CLOSED | `pipeline/parallel.py`, `test_parallel.py`; selected suite PASS |
| RV04 | URD 0.1/UC_04 | Hardware encoder chưa biết capability/chất lượng | Medium | Không bật ở Lát A; benchmark/canary ở Lát E | DEFERRED | Q05 |
| RV05 | URD 0.1 | API, DB JSON, auth và job legacy có compatibility path | Medium | Giữ thay đổi additive, test job không metrics/manifest | CLOSED | URD BR01_03, BR03_03, NFR_07 |
| RV06 | Checkpoint Lát B | Cache bản dịch lỗi có thể ngăn provider phục hồi khi retry | Critical | Chỉ commit translation sau `ensure_translation_complete`; validator từ chối fallback/failed/skipped-only | CLOSED | `checkpoint_payloads.py`, `test_checkpoint_payloads.py` |
| RV07 | Checkpoint Lát B | Artifact path trong manifest có thể thoát work dir | High | Resolve + `relative_to(root)` và test `../outside.json` | CLOSED | `stage_checkpoint.py`, `test_stage_checkpoint.py` |
| RV08 | Checkpoint Lát B2 | JSON hợp lệ nhưng cue WAV/voice track/mixed audio/render thiếu hoặc corrupt vẫn có thể bị reuse | Critical | Manifest ghi checksum từng dependency và bundle checksum; mọi mismatch thành cache miss | CLOSED | `stage_checkpoint.py`, TC_019 |
| RV09 | Checkpoint Lát B2 | Render thẳng ra output có thể để file dở và mất khả năng resume khi retry xóa output | High | Commit MP4 trong work dir, sau đó copy atomically ra output | CLOSED | `artifact_io.py`, TC_022 |
| RV10 | Checkpoint Lát B2 | Phục hồi nguyên segment từ TTS checkpoint có thể ghi đè layout/style hiện tại | High | Chỉ serialize/merge allow-list field voice/TTS/timeline theo index | CLOSED | `checkpoint_payloads.py`, TC_021 |
| RV11 | Checkpoint Lát B2 | Cấu hình mới ghi cùng tên WAV/MP4 có thể làm hỏng dependency của checkpoint cũ trước khi stage mới commit | Critical | Tách TTS/audio/render artifact theo fingerprint; validate dependency trước khi ghi JSON/manifest | CLOSED | `main.py`, `stage_checkpoint.py`, dependency preservation unit test |
| RV12 | SIT local | Homebrew FFmpeg thiếu subtitles/drawtext và Python host chưa cài Pillow/edge-tts | High | Xác nhận requirements/Docker đã khai báo; chạy Pillow fallback trong virtualenv tạm; thêm preflight/provisioning check vào backlog | OPEN | TC_023 PASS sau remediation tạm; Docker NOT_RUN |
| RV13 | SIT kill/restart | Audio TTS/mix ngắn hơn source và render dùng `-shortest`, làm video bị truncate | Critical | Đề xuất pad/trim mixed audio đúng source duration, bump fingerprint contract và retest | OPEN | TC_024 recovery PASS; TC_025 duration FAIL 12s→3,296s |
| RV14 | Retest 2026-10-18 | Audio generated-only ngắn hơn source | Critical | `apad` + `atrim` theo source duration; bump audio/render fingerprint | CLOSED | TC_028 PASS, 2,000s→2,000s |
| RV15 | Retest 2026-10-18 | TTS checkpoint không reuse sau SIGKILL dù fingerprint không đổi | High | Giữ FAIL, điều tra validator/dependency state trước khi tuyên bố recovery hoàn tất | OPEN | TC_030 |
