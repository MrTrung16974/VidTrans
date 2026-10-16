# Implementation — Tối ưu pipeline media, Lát A–B2

- Ngày: 2026-10-16
- Plan: 0.1 APPROVED
- URD/DESIGN: 0.1
- Phạm vi: instrumentation + P0 hardening + checkpoint per-job cho transcript/translation/TTS/audio/render; chưa tối ưu thuật toán.

## Thay đổi

- Thêm `pipeline/stage_metrics.py`:
  - allow-list stage;
  - đo wall time bằng monotonic clock;
  - tính RTF khi biết duration;
  - phân biệt completed/failed/cancelled;
  - lỗi telemetry không che lỗi pipeline.
- `process_video` lưu `stage_metrics` additive cho preflight, OCR, ASR, translation, summary, voice routing, TTS, audio mix, subtitle artifact và render.
- Thêm `infrastructure/media_probe.py` để lấy duration, kích thước, fps và audio presence bằng một lần ffprobe trong luồng chính.
- Truyền source probe xuống audio mix và FPS xuống Pillow fallback, tránh probe source lặp lại trong cùng job.
- Thêm `pipeline/parallel.py`; TTS thu `future.result()` bằng `as_completed`, hủy pending khi lỗi và propagate đúng exception gốc với note cue index đã sanitize.
- Thêm `infrastructure/artifact_io.py`; transcript và translation JSON dùng temp file + flush/fsync + atomic replace.
- Không thêm biến môi trường, DB migration, endpoint, dependency hoặc external call mới.
- Thêm `infrastructure/stage_checkpoint.py`:
  - manifest versioned trong work dir của job;
  - SHA-256 source/artifact và canonical fingerprint;
  - checksum/schema/path validation trước reuse;
  - cache miss an toàn khi manifest/artifact corrupt hoặc config đổi.
- Retry/restart cùng job dùng lại transcript và bản dịch hoàn chỉnh; `stage_cache` ghi hit/miss.
- Bản dịch `source_fallback`/failed/skipped-only không được commit hoặc reuse.
- Source hash kiểm tra cancel giữa các chunk; xóa job hiện hữu đã xóa toàn work dir nên cũng xóa checkpoint.
- Manifest hỗ trợ bundle dependency có checksum: TTS gồm cue WAV + voice track, audio gồm mixed WAV, render gồm MP4 trong work dir.
- Bundle checksum được đưa vào fingerprint downstream để dependency đổi/corrupt sẽ invalidate đúng chuỗi.
- TTS checkpoint chỉ merge allow-list voice/TTS/timeline lên segments layout hiện tại và phục hồi `Path` từ relative path an toàn.
- Retry có TTS/audio/render hit bỏ qua provider/FFmpeg tương ứng; trạng thái hit/miss ghi additive vào `stage_cache`.
- Render commit vào work dir rồi copy atomically ra output public; output bị xóa khi retry vẫn phục hồi được mà không encode lại.
- Media artifact được tách theo stage fingerprint; lần chạy mới lỗi giữa chừng không ghi đè dependency của checkpoint hợp lệ trước đó.

## Unit test mới

- `backend/tests/test_stage_metrics.py`
- `backend/tests/test_parallel.py`
- `backend/tests/test_artifact_io.py`
- `backend/tests/test_media_probe.py`
- `backend/tests/test_stage_checkpoint.py`
- `backend/tests/test_checkpoint_payloads.py`

## Kiểm chứng đã chạy

### Suite feature + pipeline liên quan

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 -m unittest \
  backend.tests.test_stage_metrics backend.tests.test_parallel \
  backend.tests.test_artifact_io backend.tests.test_media_probe \
  backend.tests.test_media_validation backend.tests.test_tts_timing \
  backend.tests.test_asr backend.tests.test_translation_pipeline \
  backend.tests.test_stage_checkpoint backend.tests.test_checkpoint_payloads
```

- Kết quả mới nhất của nhóm feature tương ứng: PASS, 58/58 tests.
- Log exception trong case callback telemetry lỗi là expected evidence; suite exit code 0.

### Regression chọn lọc sau Lát B

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 -m unittest \
  backend.tests.test_job_store backend.tests.test_job_service \
  backend.tests.test_job_scheduler backend.tests.test_domain_models \
  backend.tests.test_languages backend.tests.test_ocr_pipeline \
  backend.tests.test_subtitle_layout backend.tests.test_voice_routing \
  backend.tests.test_segment_filtering backend.tests.test_frontend_static
```

- Kết quả gộp cuối: PASS, 137/137 tests, bao gồm feature, checkpoint và regression pipeline/job.

### Kiểm tra khác

- Parse AST không sinh `.pyc` cho các module đổi/thêm: PASS.
- Import `backend/main.py` với bytecode bị tắt: PASS.
- Probe file local H.264 1080p: PASS, đọc 1920×1080, 643,6 giây, 30 fps, có audio.
- `git diff --check`: PASS.
- Một `.pyc` tracked bị `compileall` chạm vào đã được khôi phục riêng; không còn trong diff.

## Giới hạn và việc tiếp theo

- Chưa benchmark full pipeline vì sẽ gọi model/provider và chưa có golden corpus/hardware profile được duyệt.
- Chưa đo CPU/RSS/temp disk hoặc provider request count.
- Đã chạy live provider/gTTS/FFmpeg/Pillow smoke trên dữ liệu tổng hợp và kill-after-TTS/restart trong backend/SQLite/work/output cô lập.
- Recovery PASS: process -9, restart_count=1, transcript/translation/TTS hit; audio/render chạy lại.
- E2E phát hiện `DF_001`: source 12 giây tạo output 3,296 giây vì audio không pad và render dùng `-shortest`; chưa sửa trong lượt test/phân tích này.
- Chưa phủ kill tại mọi stage, Docker build, ASR/OCR/provider model thật hoặc benchmark corpus.
- ThreadPool TTS vẫn giữ 5 để tránh thay đổi tuning khi chưa có baseline.
- Chưa chạy Docker/full end-to-end; unit test không thay thế SIT/UAT.
