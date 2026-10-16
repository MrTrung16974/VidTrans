# Phân tích tối ưu pipeline nhận diện, dịch và ghép video

- Feature: `media-pipeline-optimization`
- Version: `0.1`
- Trạng thái: `DRAFT`
- Ngày phân tích: 2026-10-16
- Nguồn yêu cầu: Người dùng yêu cầu phân tích và lập plan tối ưu quy trình nhận diện tiếng, dịch và ghép lại vào video.
- Phạm vi phiên này: phân tích và lập kế hoạch; chưa thay đổi source code, chưa chạy benchmark nặng, chưa triển khai.

## 1. Mục tiêu

Tối ưu đồng thời bốn đặc tính của pipeline:

1. Giảm thời gian xử lý và tài nguyên trên mỗi phút video.
2. Tăng độ chính xác của transcript, bản dịch và đồng bộ giọng/phụ đề.
3. Khi retry hoặc service restart, chỉ chạy lại stage bị lỗi và các stage phụ thuộc.
4. Có số liệu để chứng minh cải thiện, phát hiện regression và chọn cấu hình theo hạ tầng.

Không đặt mục tiêu tốc độ tuyệt đối trước khi có benchmark trên cùng máy. Mục tiêu hiệu năng trong tài liệu này là mục tiêu tương đối so với baseline Phase 0.

## 2. Nguồn bằng chứng

| ID | Nguồn | Kết luận |
|---|---|---|
| SRC_01 | `ARCHITECTURE.md` | Stage-level cache/checkpoint, media adapter và provider interface đã nằm trong hướng kiến trúc nhưng chưa hoàn tất. |
| SRC_02 | `backend/main.py::process_video` | OCR/ASR → dịch → summary → TTS/mix → render đang được điều phối trong một hàm lớn; retry gọi lại luồng từ đầu. |
| SRC_03 | `backend/pipeline/asr.py` | Đã có faster-whisper int8, model cache, VAD và fallback; một lần thất bại có thể chạy lại toàn bộ media tối đa ba cấu hình. |
| SRC_04 | `backend/pipeline/ocr.py` | OCR giải nén toàn bộ ROI thành JPEG trên đĩa rồi đọc tuần tự; bỏ qua OCR frame giống nhau chỉ sau khi frame đã được decode và ghi file. |
| SRC_05 | `backend/pipeline/translation.py` | Đã batch, deduplicate và retry; cache chỉ sống trong một lần gọi/job, chưa dùng lại giữa retry hoặc job trùng nội dung. |
| SRC_06 | `backend/infrastructure/vietnamese_translator.py` | Provider hiện dùng request tuần tự, có khoảng nghỉ mặc định và fallback; quota/rate limit có thể chi phối tổng thời gian. |
| SRC_07 | `backend/main.py::synthesize_tts_segments` | TTS chạy ThreadPool cố định 5; mỗi cue có gọi mạng, FFmpeg và nhiều lần ffprobe. `wait(futures)` chưa đọc `future.result()`, nên lỗi trong worker có thể bị trì hoãn/che khuất. |
| SRC_08 | `backend/main.py::burn_subtitles` | Đường chuẩn render một lần bằng libx264; fallback Pillow bung video thành PNG theo frame, tốn I/O và dung lượng rất lớn. |
| SRC_09 | `backend/application/job_scheduler.py`, `backend/app/config.py` | Có worker pool và semaphore cho Whisper/OCR; chưa có resource class/budget riêng cho TTS, FFmpeg và render. |
| SRC_10 | Mẫu local trong `backend/uploads` | Có 5 video H.264 1920×1080, 30 fps; bốn video dài 643,612 giây và một video dài 783,370 giây. Đây chỉ là corpus sẵn có, chưa đủ đại diện và chưa phải benchmark. |

## 3. AS-IS

```text
video
  -> kiểm tra stream nhiều lần bằng ffprobe
  -> [Auto/OCR Trung] tách hàng nghìn JPEG -> PaddleOCR tuần tự
  -> Whisper toàn video -> lọc cue -> transcript.json
  -> dịch batch/tuần tự -> translation.json
  -> [mode lồng tiếng] phân loại giọng -> TTS từng cue
       -> trim từng cue bằng FFmpeg -> ffprobe -> atempo tùy cue -> ffprobe
  -> dựng voice track -> mix audio
  -> ghi SRT/ASS
  -> encode lại toàn video và mux audio
  -> output
```

### Điểm mạnh hiện có

- faster-whisper int8, VAD, beam có cấu hình và model được giữ trong bộ nhớ.
- Queue/job đã bền vững, claim nguyên tử, có cancel và giới hạn concurrency cho Whisper/OCR.
- Translation đã batch, deduplicate cue, retry/backoff và chặn output chưa dịch.
- OCR đã có ROI, confidence, consensus nhiều frame và đối chiếu ASR.
- Render chuẩn đã ghép burn subtitle và audio trong một lần encode cuối.
- Artifact transcript/translation đã tồn tại, tạo nền tốt cho checkpoint.

### Vấn đề ưu tiên

| Ưu tiên | Finding | Tác động |
|---|---|---|
| P0 | Chưa đo thời gian từng stage, real-time factor, cache hit, provider request/retry, CPU/RAM/disk. | Không xác định được bottleneck thật theo máy và không chứng minh được cải thiện. |
| P0 | Transcript và translation đã được ghi file nhưng không được nạp lại như checkpoint hợp lệ. | Restart/retry sau lỗi TTS/render vẫn chạy lại ASR và dịch. |
| P0 | TTS worker futures không được gọi `result()`. | Lỗi cue có thể không fail ngay tại nguyên nhân; gây output thiếu hoặc lỗi muộn, khó retry đúng stage. |
| P1 | OCR tạo toàn bộ frame JPEG trước khi phát hiện frame giống nhau. | I/O và disk amplification cao; video 10–13 phút ở 5 fps sinh khoảng 3.200–3.900 ảnh. |
| P1 | Auto OCR vẫn chạy ASR toàn video để đối chiếu. | Chất lượng tốt hơn nhưng chi phí gần bằng tổng OCR + ASR, kể cả khi OCR rất tin cậy. |
| P1 | ASR fallback chạy lại toàn bộ media và luôn bật word timestamps. | Có thể lặp decode/inference; mode chỉ làm phụ đề không luôn cần word-level timing. |
| P1 | Cache dịch chỉ trong job; request provider tuần tự có throttling. | Retry và video có nội dung trùng vẫn trả lại chi phí mạng/quota. |
| P1 | TTS không có cache; concurrency cố định 5; mỗi cue phát sinh 1 request mạng, 1–2 FFmpeg và 1–2 ffprobe. | Latency tăng tuyến tính theo số cue, dễ bị rate limit và tranh CPU với render/job khác. |
| P1 | Render CPU libx264 cố định; fallback Pillow bung toàn bộ frame PNG. | Encode là stage cuối dài; fallback có thể làm đầy đĩa. |
| P2 | Media metadata được đọc bằng nhiều ffprobe rời rạc. | Overhead tiến trình nhỏ trên video dài nhưng lớn với batch nhiều video/cue TTS. |
| P2 | Scheduler giới hạn theo job và hai stage, chưa có budget CPU/RAM/I/O cho FFmpeg/TTS/render. | Hai job có thể đồng thời tạo nhiều subprocess và làm throughput toàn hệ thống kém hơn. |
| P2 | Điều phối pipeline còn tập trung trong `main.py`. | Khó test stage độc lập, khó cache/checkpoint và khó chuyển sang worker riêng. |

## 4. TO-BE đề xuất

```text
Preflight/probe một lần
  -> Stage runner + fingerprint + metrics
  -> Source acquisition checkpoint
  -> Audio normalization checkpoint
  -> OCR/ASR chiến lược thích nghi
  -> Transcript checkpoint
  -> Translation cache + translation checkpoint
  -> Subtitle layout checkpoint
  -> TTS cache + bounded synthesis + audio checkpoint
  -> Render theo capability đã probe
  -> Artifact validation
```

Mỗi stage nhận input contract bất biến, tạo output manifest và chỉ được reuse khi fingerprint khớp. Fingerprint tối thiểu gồm hash/version của input, tham số ảnh hưởng kết quả, engine/model/provider và schema version. File tạm phải được ghi atomically rồi mới đánh dấu stage hoàn tất.

### 4.1 Đo lường và benchmark

Thu thập cho từng job/stage:

- wall time, queue wait, real-time factor (`stage_seconds / video_seconds`);
- số cue/frame/provider request/retry, cache hit/miss;
- peak RSS, CPU, dung lượng temp/output và số subprocess;
- detected language/confidence, ASR low-confidence rate, translation fallback/review rate;
- TTS overflow/drift, A/V duration delta và render fps.

Corpus benchmark tối thiểu nên có video ngắn/dài, ngang/dọc, có/không nhạc, thoại rõ/nhiễu, nguồn trong 8 ngôn ngữ, OCR Trung rõ/mờ và ba processing mode. Dữ liệu đánh giá phải được phép sử dụng và không đưa PII vào log/report.

### 4.2 Nhận diện giọng nói và OCR

- Probe media một lần và tạo audio mono chuẩn dùng lại cho ASR và voice analysis khi phù hợp.
- Chỉ bật `word_timestamps` khi downstream thực sự dùng word timing.
- Thay ba lần full retry bằng chính sách theo lỗi: VAD fallback/chunk retry có checkpoint; không lặp toàn video khi chỉ một chunk lỗi.
- Với `source=auto`, chạy language detection ngắn trước, lưu confidence; confidence thấp mới dùng model/cấu hình mạnh hơn.
- OCR chuyển từ “extract tất cả JPEG” sang stream/pipe hoặc chunk nhỏ; phát hiện thay đổi ảnh trước khi gọi OCR và xóa chunk ngay sau commit.
- Chế độ Auto dùng chiến lược thích nghi: OCR confidence/coverage cao thì chỉ ASR các vùng thiếu hoặc bất đồng; confidence thấp mới chạy ASR toàn video.
- Giữ đường `quality` cho người dùng cần đối chiếu OCR + ASR toàn phần; không hy sinh độ chính xác ngầm để đổi lấy tốc độ.

### 4.3 Dịch

- Tạo provider interface chính thức với capability: batch, max bytes/chars, rate limit và health/circuit state.
- Persistent cache theo `normalized_source + source_lang + target_lang + provider/model + prompt/glossary version`; không cache failure vĩnh viễn.
- Batch theo giới hạn byte thật, giữ stable cue ID và context lân cận nhưng output vẫn map chính xác về cue.
- Hỗ trợ glossary/tên riêng theo job hoặc tenant; validator theo ngôn ngữ giữ cơ chế `needs_review`.
- Giới hạn concurrency theo provider; không tăng song song mù vì có thể làm rate-limit nặng hơn.

### 4.4 TTS và audio

- Trước hết sửa propagation lỗi futures và ghi trạng thái theo cue.
- Bounded executor có cấu hình theo provider/CPU; tách semaphore network TTS và semaphore FFmpeg.
- Cache audio theo text, ngôn ngữ, voice, rate, pitch, engine version; retry chỉ cue lỗi.
- Giảm ffprobe theo cue: lấy duration theo batch/manifest hoặc suy ra duration sau atempo khi an toàn.
- Benchmark việc gộp trim/normalize/atempo và ghép voice track theo chunk để tránh command/filter graph quá lớn.
- Fail-fast khi tỷ lệ cue lỗi hoặc overflow vượt ngưỡng; không âm thầm xuất video thiếu lời.

### 4.5 Render và điều phối tài nguyên

- Capability probe lúc startup cho libass/drawtext/hardware encoder; production không đợi đến cuối job mới phát hiện thiếu filter.
- Giữ một lần encode cuối; benchmark `libx264` preset/CRF và encoder phần cứng trên cùng bộ kiểm thử chất lượng.
- Đặt quota temp disk và cleanup theo stage; Pillow fallback chỉ dùng cho video nhỏ hoặc fail rõ khi vượt giới hạn.
- Scheduler áp resource class (`network`, `asr`, `ocr`, `tts-ffmpeg`, `render`) và giới hạn tổng subprocess/CPU/RAM thay vì chỉ đếm job.
- Tách stage orchestration/media adapter khỏi route và `main.py`, giữ public API tương thích.

## 5. Mục tiêu nghiệm thu đề xuất

Các ngưỡng dưới đây được chốt sau Phase 0, nhưng plan mặc định dùng các guardrail sau:

| Nhóm | Mục tiêu |
|---|---|
| Hiệu năng | Giảm ít nhất 30% p50 và 20% p95 end-to-end trên cùng corpus/hardware, theo từng processing mode. |
| Retry | Lỗi sau translation không chạy lại ASR/dịch khi fingerprint không đổi; thời gian retry xấp xỉ tổng các stage còn lại. |
| ASR | WER/CER không tăng quá 1 điểm phần trăm tuyệt đối so với baseline; không tăng low-confidence/review rate ngoài ngưỡng đã chốt. |
| Dịch | Không tăng `source_fallback`; kiểm thử tham chiếu/human rubric không giảm quá ngưỡng đã chốt; stable cue mapping đạt 100%. |
| TTS | 100% lỗi worker được propagate; không có cue mất âm thầm; p95 drift/overflow không xấu hơn baseline. |
| Render | Output phát được, đúng duration/timeline, không mất audio/subtitle; SSIM/VMAF hoặc kiểm tra tương đương đạt ngưỡng theo encoder đã chọn. |
| Vận hành | Không vượt disk quota; metric đủ truy ra stage chậm/lỗi mà không log transcript/secret. |

## 6. Phạm vi

### Trong phạm vi

- Pipeline local từ media đã tải đến output video.
- ASR, OCR Trung hiện hữu, translation, subtitle layout, TTS, audio mix và render.
- Stage metrics, cache/checkpoint, retry/cancel và resource coordination.
- API/job metadata/UI progress cần thiết để hiển thị stage/cache/retry.
- Unit, integration, benchmark và regression test cô lập.

### Ngoài phạm vi hiện tại

- Thêm ngôn ngữ mới ngoài catalog đã có.
- OCR đa ngôn ngữ ngoài tiếng Trung.
- Thay đổi nghiệp vụ tải nguồn hoặc đăng mạng xã hội.
- Redis/multi-host ngay trong lát đầu; contract phải sẵn sàng nhưng local SQLite/filesystem được ưu tiên.
- Deploy production trong phiên lập plan.

## 7. Rủi ro và nguyên tắc kiểm soát

| Rủi ro | Kiểm soát |
|---|---|
| Cache trả artifact cũ/sai | Fingerprint đầy đủ, schema version, atomic commit, checksum và invalidation test. |
| Tối ưu tốc độ làm giảm chất lượng | Golden corpus, quality gate độc lập cho từng stage và feature flag để rollback. |
| Cache chứa nội dung người dùng | Quy định TTL/quota, quyền file, xóa theo job/tenant; không dùng transcript làm metric label/log. |
| Hardware encoder khác màu/chất lượng | Capability probe và benchmark; CPU path luôn là fallback được kiểm chứng. |
| Parallelism gây rate limit/OOM | Adaptive/bounded concurrency, semaphore theo resource và circuit breaker. |
| Checkpoint dở dang sau crash | Ghi file tạm + rename atomically; manifest chỉ commit sau validation. |
| Thay đổi lớn trong `main.py` gây regression | Tách theo lát nhỏ, giữ adapter compatibility và shadow/feature flag. |

## 8. Giả định và câu hỏi cần người dùng review

- `ASSUMPTION A01`: Ưu tiên tổng throughput và khả năng retry, nhưng không giảm chất lượng nhận diện/dịch hiện tại.
- `ASSUMPTION A02`: Cho phép lưu cache nội dung/audio cục bộ có TTL trong cùng tenant/deployment.
- `ASSUMPTION A03`: Hạ tầng mục tiêu trước mắt vẫn là một VPS/container; GPU/hardware encoder là tùy chọn.
- `OPEN Q01`: Mục tiêu chính là giảm thời gian cho một video hay tăng số video xử lý đồng thời?
- `OPEN Q02`: Có cho phép chế độ `Fast/Balanced/Quality`, trong đó `Quality` vẫn đối chiếu OCR + ASR toàn phần?
- `OPEN Q03`: Cache transcript/bản dịch/TTS được giữ bao lâu và có được dùng lại giữa hai job của cùng khách hàng không?
- `OPEN Q04`: Bộ video nào được phép dùng làm golden corpus và có transcript/bản dịch chuẩn để đo WER/CER/chất lượng dịch?
- `OPEN Q05`: VPS production có CPU/RAM/GPU nào và có encoder `h264_nvenc`, `h264_videotoolbox` hoặc `h264_qsv` không?

Các câu hỏi trên ảnh hưởng cách chốt ngưỡng và lựa chọn kỹ thuật, nhưng không chặn Phase 0 đo baseline và P0 hardening.
