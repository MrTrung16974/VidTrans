# Thiết kế — Tối ưu pipeline media, Lát A–B2

- PLAN: 0.1 APPROVED ngày 2026-10-16.
- URD: 0.1 DRAFT, phần UC_01, BR03_04, BR04_01, NFR_05–07 đủ rõ để triển khai độc lập.
- Phạm vi coding: stage metrics nền, TTS exception propagation, atomic artifact, source media probe tổng hợp và checkpoint per-job cho transcript/translation/TTS/audio/render.
- Ngoài lát: adaptive OCR/ASR, cross-job translation/TTS cache, hardware encoder và resource-class scheduler.

## 1. Repository và module chịu trách nhiệm

| Yêu cầu | Điểm hiện tại | Thiết kế |
|---|---|---|
| UC_01/BR01_01-03 | `backend/main.py::process_video`, `JobService` | Tạo `pipeline/stage_metrics.py` thuần Python; `main.py` adapter persist map additive vào job. |
| BR03_04 | `main.py::synthesize_tts_segments` | Tạo helper `pipeline/parallel.py` thu kết quả futures và propagate exception gốc; main dùng helper thay `wait()` thuần. |
| BR02_04/NFR_06 | Ghi JSON trực tiếp bằng `Path.write_text` | Tạo `infrastructure/artifact_io.py::write_json_atomic`; dùng cho transcript và translation artifacts. |
| BR04_01 | Nhiều helper ffprobe rời trong `main.py` | Tạo `infrastructure/media_probe.py` đọc JSON một lần; truyền duration/dimensions/audio vào luồng chính/mix/render fallback. |
| UC_02/BR02_01-04 | Retry hiện chạy lại từ đầu | Tạo `infrastructure/stage_checkpoint.py`; manifest per-job validate fingerprint/checksum/schema trước khi reuse transcript/translation/TTS/audio/render. |

Giữ source/test trong `backend/`; thư mục `DEV/` chỉ chứa tài liệu.

## 2. Contract

### Stage measurement

```text
StageMeasurement {
  stage: allow-listed string,
  status: completed | failed | cancelled,
  duration_seconds: non-negative float,
  real_time_factor?: non-negative float,
  recorded_at: UTC ISO-8601
}
```

Context manager luôn propagate exception gốc. Callback persist được bọc lỗi và không được mask lỗi pipeline.

Stage trong Lát A: `preflight`, `ocr`, `asr`, `translation`, `summary`, `voice_routing`, `tts`, `audio_mix`, `subtitle_artifacts`, `render`.

### Media probe

```text
MediaProbe(duration, width, height, fps, has_audio)
```

Probe dùng `ffprobe -show_entries` JSON, validate video stream/duration/kích thước, parse rational FPS và phát hiện audio stream. Lỗi trả `MediaProbeError` không chứa command output dài hoặc path nhạy cảm.

### Atomic JSON

- Tạo parent nếu cần.
- Ghi UTF-8 vào file tạm cùng directory, flush + `fsync`, `os.replace` sang đích.
- Dọn file tạm trong `finally`.
- Không thay đổi format JSON hiện tại (`ensure_ascii=False`, indent 2).

### Checkpoint per-job

```text
stage_manifest.json {
  version: 1,
  stages: {
    transcript: {fingerprint, artifact, checksum, metadata, completed_at},
    translation: {fingerprint, artifact, checksum, metadata, completed_at},
    tts: {fingerprint, artifact, checksum, dependencies[], bundle_checksum, metadata, completed_at},
    audio_mix: {fingerprint, artifact, checksum, dependencies[], bundle_checksum, metadata, completed_at},
    render: {fingerprint, artifact, checksum, dependencies[], bundle_checksum, metadata, completed_at}
  }
}
```

- Manifest và artifact chỉ được resolve bên trong `work/<job_id>`.
- Source fingerprint dùng SHA-256 nội dung file; stage fingerprint dùng canonical JSON SHA-256.
- Transcript fingerprint gồm source checksum, subtitle source/OCR config, Whisper model/source language và schema version.
- Translation fingerprint gồm checksum transcript checkpoint, source/target language, translator contract/schema version.
- Mismatch/corrupt/unsupported manifest là cache miss, không làm job fail và không xóa artifact khác.
- Transcript compatibility file vẫn là list segments; checkpoint dùng artifact riêng có metadata detected language/source method.
- Translation checkpoint lưu segments trước subtitle layout/TTS để retry không giữ path/timing phát sinh từ lần render cũ.
- TTS checkpoint lưu state theo index cue và đường dẫn tương đối tới audio đã fit; không ghi absolute path. Bundle bắt buộc có mọi cue audio được tham chiếu và `voice_track.wav`.
- Audio checkpoint lưu đường dẫn tương đối tới `mixed_audio.wav`; fingerprint gồm TTS bundle, source, mode, volume, keep-original-audio và checksum nhạc nền khi có.
- Render checkpoint lưu video hoàn chỉnh trong work directory; fingerprint gồm source, ASS checksum, audio bundle, subtitle style, FPS và renderer contract. Output public được copy atomically từ artifact đã kiểm tra.
- `bundle_checksum` là hash canonical của checksum JSON chính và toàn bộ dependency. Downstream dùng giá trị này để invalidation lan truyền kể cả khi JSON không đổi nhưng WAV/MP4 đổi.
- File dependency thiếu, checksum sai hoặc thoát khỏi work directory đều là cache miss; manifest cũ không có dependencies vẫn tương thích cho transcript/translation.

## 3. Luồng và concurrency

- `process_video` probe source một lần trước OCR/ASR.
- Sau preflight, runner tính source checksum và thử load transcript checkpoint.
- Transcript hit bỏ qua OCR/ASR; translation hit bỏ qua provider; TTS hit bỏ qua provider/FFmpeg cue và dựng voice track; audio hit bỏ qua mix; render hit bỏ qua encode. Mỗi hit được ghi additive vào `stage_cache` của job.
- Fingerprint downstream phụ thuộc checksum artifact upstream nên thay transcript tự invalid translation.
- Mỗi stage dùng context manager; callback đọc job hiện tại, merge `stage_metrics` và update atomically ở top-level qua store.
- TTS vẫn dùng ThreadPool 5 trong lát này để không đổi tuning chưa benchmark.
- Helper futures thu kết quả bằng `as_completed`; exception đầu tiên hủy pending futures khi có thể và re-raise exception gốc. Running future có thể kết thúc trước khi executor shutdown, nhưng pipeline không render output thành công.
- Không thêm retry mới hoặc side effect ngoài hiện tại.
- Voice routing vẫn chạy trước TTS lookup vì kết quả route là input của fingerprint; đây là phép phân tích local nhỏ hơn đáng kể so với tổng hợp giọng.
- TTS state chỉ merge các field voice/TTS/timeline được allow-list vào segments layout hiện tại, tránh phục hồi layout cũ.

## 4. Error handling và cancellation

- `JobCancelled` được nhận dạng theo tên class trong metric thành `cancelled`; exception gốc được giữ.
- TTS future ném `JobCancelled` hoặc provider/FFmpeg error đều nổi lên caller.
- Metric persistence lỗi chỉ log exception với stage/job ID, không log source text.
- Atomic write fail giữ artifact cũ nếu đã có và không để file đích mới bị ghi dở.
- Checkpoint chỉ commit sau khi stage hoàn tất. Lỗi TTS/mix/render không ghi record mới; record cũ khác fingerprint không được dùng.
- TTS/audio/render artifact nằm trong path có stage fingerprint; lần chạy cấu hình mới không ghi đè dependency của checkpoint đã commit trước đó.
- Copy render ra output dùng temp file cùng thư mục + `fsync` + `os.replace`; copy lỗi giữ output cũ nguyên vẹn.

## 5. API, DB, auth và compatibility

- API: không thêm endpoint; status/job có field additive `stage_metrics` vì response hiện trả payload job.
- DB: không migration; payload JSON hiện hữu chứa field mới.
- Authz: không thay đổi; endpoint status/download vẫn dùng middleware hiện hữu.
- Job cũ không có `stage_metrics` vẫn đọc/chạy bình thường.
- Các helper `get_media_duration`, `has_audio_stream` giữ lại cho TTS artifact và caller compatibility; source video chính dùng probe tổng hợp.

## 6. Cấu hình

Không thêm biến môi trường trong Lát A. Concurrency TTS cố định 5 được ghi nhận là debt cho Lát D, tránh tự chốt default mới khi chưa có baseline.

## 7. Test strategy

- `test_stage_metrics.py`: success/RTF, failure propagation, cancellation classification, callback failure không mask pipeline error.
- `test_parallel.py`: kết quả theo index và propagate exact worker exception/cancel pending.
- `test_artifact_io.py`: JSON Unicode, replace atomically, cleanup temp khi replace/write lỗi bằng mock.
- `test_media_probe.py`: video+audio JSON, no audio, invalid/no video, rational FPS.
- `test_stage_checkpoint.py`: commit/load, mismatch, checksum corrupt, manifest corrupt, path traversal và source hash.
- `test_stage_checkpoint.py`: thêm dependency missing/corrupt, bundle checksum và traversal dependency.
- `test_artifact_io.py`: thêm atomic binary copy và bảo toàn đích khi replace lỗi.
- `test_checkpoint_payloads.py`: round-trip TTS state/path và schema audio/render.
- Integration thuần helper: transcript/translation checkpoint schema reject payload sai.
- Regression chọn lọc: ASR, translation, TTS timing, media validation và import/compile main.

Không gọi provider thật, không tải model, không đọc DB/config/secret production.

## 8. Tradeoff và review

- Persist metric theo stage tạo thêm một SQLite update/stage; chấp nhận trong Lát A để có baseline, tối ưu batching sau khi đo.
- Chưa đo peak RSS/CPU vì cần collector/runtime profile; Lát A ưu tiên wall time/RTF và correctness.
- Render artifact tồn tại cả trong work và output để giữ ranh giới checkpoint/public artifact; đổi lại tăng tạm thời dung lượng đĩa gần bằng một output video.
- Probe tổng hợp không thay thế validation upload; nó cung cấp metadata runtime và phát hiện source bị thay/hỏng trước xử lý.
- Open questions Q01–Q05 không chặn Lát A; chúng chặn các lựa chọn thuật toán/cache/hardware tiếp theo.
