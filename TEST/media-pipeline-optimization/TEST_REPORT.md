# Test report — Tối ưu pipeline media, Lát A–B2

- Ngày chạy: 2026-10-16
- Môi trường: local workspace, Python host; live provider chạy với dữ liệu tổng hợp; không gọi ASR/model thật.
- Build/commit: working tree hiện tại, chưa commit.
- Test cases: `TESTCASES.md` version 0.1.

## Kết quả

| Nhóm | PASS | FAIL | BLOCKED/NOT_RUN | Bằng chứng |
|---|---:|---:|---:|---|
| Feature/checkpoint + regression chọn lọc | 137 | 0 | 0 | unittest exit code 0 |
| Python AST/import | 2 | 0 | 0 | AST parse và import `main` exit code 0 |
| Media probe local smoke | 1 | 0 | 0 | Metadata 1920×1080/643,6s/30fps/audio |
| Live translation + gTTS + render/mux | 1 | 0 | 0 | H.264/AAC 640×360, 4 giây; ffprobe xác nhận |
| Kill/restart checkpoint recovery | 1 | 0 | 0 | `SIGKILL=-9`; restart_count=1; upstream/TTS cache hit |
| Media duration sau recovery | 0 | 1 | 0 | Source 12 giây → output 3,296 giây |
| Patch hygiene | 1 | 0 | 0 | `git diff --check` |
| Benchmark/quality corpus | 0 | 0 | 1 | Q04/Q05 |

Tổng unit/regression mới nhất: 137/137 PASS.

## Mapping testcase

- TC_001–003: PASS qua `test_stage_metrics.py`.
- TC_004–005: PASS qua `test_parallel.py`.
- TC_006–007: PASS qua `test_artifact_io.py`.
- TC_008–010: PASS qua `test_media_probe.py`.
- TC_011: PASS với 79 regression tests chọn lọc.
- TC_012: BLOCKED; chưa có golden corpus và production-like hardware.
- TC_013–017: PASS qua `test_stage_checkpoint.py`.
- TC_018: PASS qua `test_checkpoint_payloads.py`.
- TC_019–020: PASS qua dependency checksum/path validation trong `test_stage_checkpoint.py`.
- TC_021: PASS qua TTS state round-trip trong `test_checkpoint_payloads.py`.
- TC_022: PASS qua atomic binary copy trong `test_artifact_io.py`.
- TC_023: PASS. Provider dịch thật 2,995 giây; gTTS 0,369 giây; render/mux 3,890 giây. Output có H.264 video + AAC audio, duration 4 giây.
- TC_024: PASS. Process con bị `SIGKILL` sau khi TTS commit; restart reuse transcript/translation/TTS, chạy lại audio/render và hoàn tất job.
- TC_025: FAIL. Source 12 giây nhưng output chỉ 3,296 giây, delta 8,704 giây.

## Finding môi trường SIT

- Python host ban đầu thiếu Pillow và `edge-tts` dù cả hai có trong `requirements.txt`.
- Homebrew FFmpeg 8.1 trên host thiếu cả filter `subtitles` và `drawtext`.
- Lần chạy đầu đi qua provider dịch và gTTS nhưng render BLOCKED tại fallback do thiếu Pillow.
- Cài Pillow 12.3.0 trong virtualenv tạm `/private/tmp/vidtrans-sit-venv`, không thay đổi Python hệ thống. Lần chạy lại PASS qua Pillow fallback.
- Đây là finding về provisioning local; Dockerfile đã khai báo FFmpeg/libass và project đã khai báo Pillow/edge-tts. Chưa kiểm tra Docker image trong lượt này.

## Defect

- `DF_001` — P0 media correctness: mode lồng tiếng không giữ original audio/music có thể làm output bị cắt theo độ dài TTS.
- Bằng chứng: `build_voice_track`/`mix_audio` không pad silence tới duration video; `burn_subtitles` dùng `-shortest`; fixture 12 giây cho output 3,296 giây.
- Recovery/checkpoint vẫn hoạt động đúng; defect nằm ở contract duration của audio/render, không phải ở manifest resume.
- Chưa sửa trong lượt phân tích/test này.

## Kết luận

Lát A–B2 đạt unit/regression, live provider/media smoke và checkpoint recovery sau `SIGKILL`. E2E chưa đạt media correctness vì `DF_001`; Docker build, UAT và benchmark corpus vẫn chưa chạy.

## Retest 2026-10-18

| Nhóm | Kết quả | Bằng chứng |
|---|---|---|
| Phân đoạn theo word timestamp/dấu câu/pause/final flush | PASS | 3 unit tests mới |
| Audio ngắn hơn video | PASS | Source 2,000s; voice 0,500s; mixed audio 2,000s |
| Regression chọn lọc mở rộng | PASS | 159/159 tests, 0 fail |
| Hai video hiện có — kiểm kê baseline | PARTIAL | `27099424`: 62,501s, 2 cue, max 29,630s/cue; `c863fbc1`: 783,370s, 322 cue, câu cuối 783,300s |
| Crash/restart sau TTS | FAIL | TTS fingerprint không đổi nhưng cache miss sau SIGKILL |

`DF_001` đã được sửa ở contract audio bằng `apad` + `atrim`, đồng thời bump audio/render fingerprint. Không chạy đè lại hai job người dùng vì việc đó gọi ASR/provider và thay artifact hiện có; do đó chưa có số “sau sửa” end-to-end cho chính hai video.

Video `27099424` không phải mẫu Trung→Việt hợp lệ: request ghi `zh-CN`, nhưng transcript có 0 ký tự Hán và 45 ký tự Latin. Kết quả video này chỉ là bằng chứng baseline vận hành, không dùng để kết luận chất lượng dịch Trung→Việt. Video `c863fbc1` có 1.841 ký tự Hán và phủ câu cuối tới 783,300/783,370 giây.

## Retest theo `output_3c10ceec.mp4`

- Quan sát contact sheet mỗi 2 giây: cue Việt đầu tiên dài bốn dòng còn hiển thị qua nhiều lần chữ Trung thay đổi; lỗi tái hiện rõ.
- Audio Việt được Whisper kiểm tra độc lập cho thấy có cụm nói liên tục 10,22 giây/42 từ, phù hợp với dấu hiệu nhiều câu bị gom vào một cue/TTS dài.
- Sau sửa: test source timing, max cue duration và giữ đủ từ PASS; regression tổng 162/162 PASS.
- Chưa thể render lại chính video này vì workspace không có job `3c10ceec`, video nguồn hoặc audio Trung; file được cung cấp đã burn phụ đề Việt và thay audio. Trạng thái real-video rerender: BLOCKED_BY_MISSING_SOURCE, không báo PASS giả.

## Retest audio/màu theo `output_3c10ceec (1).mp4`

- Loudness đầu ra hiện tại: -15,9 LUFS, true peak -1,2 dBFS. Do chỉ có một track AAC đã mix, không thể đo độc lập voice và source.
- Default mới: voice gain 1,10; tiếng gốc 0,50; mục tiêu là voice nhỉnh khoảng 6–7 dB trước loudnorm thay vì chênh gần 18 dB ở cấu hình cũ 1,40/0,18.
- Màu/hộp vietsub: PASS unit cho CSS→ASS, chữ trắng/hộp đen mặc định và màu tùy chọn.
- Full selected suite sau thay đổi chạy 165 cases: 164 PASS, 1 FAIL chỉ vì test còn chờ cache URL `-10`; sau khi cập nhật test sang `-11`, nhóm ảnh hưởng retest 15/15 PASS.

## Retest mất phụ đề/voice nhỏ theo `video_2026-10-18_23-22-03.mp4`

- Contact sheet 1 fps xác nhận Vietsub mất trong khoảng xấp xỉ 8–14 giây dù chữ Trung vẫn hiện trên hình.
- Whisper cưỡng bức tiếng Trung nhận lời đến 8,78 giây và trở lại từ 15,24 giây; đây là khoảng caption cháy không có lời thoại đủ rõ, nên ASR-only không thể sinh cue.
- Chế độ mặc định nguồn Trung đổi sang `auto`: OCR và Whisper được hợp nhất theo timeline; OCR thắng khi trùng thời gian, còn cue Whisper không trùng vẫn được giữ.
- Transcript fingerprint tăng từ 2 lên 3 để không dùng lại checkpoint ASR-only cũ.
- Default audio đổi thành voice gain 1,30 và tiếng gốc 0,30; mục tiêu là giọng Việt nổi rõ nhưng vẫn giữ được nền gốc.
- Test đúng tình huống khoảng im tiếng 8,8–14,8 giây PASS; nhóm ảnh hưởng 35/35 PASS trong image dự án.
- Full suite trên source hiện tại: 413 PASS, 1 FAIL và 1 ERROR không thuộc thay đổi này (`test_social_publishing` trả 422; thiếu module `infrastructure.douyin_qr_auth`).
- Chưa render lại file đã cung cấp: đây là output đã mix/burn, workspace không có source/job tương ứng; cần chạy lại video nguồn sau khi deploy image mới để nhận artifact cuối.

## Retest lỗi `No text to send to TTS API`

- Nguyên nhân: cue chỉ có dấu câu/ký hiệu vượt qua kiểm tra chuỗi không rỗng; khi Edge fallback sang gTTS, tokenizer loại hết nội dung và provider từ chối request.
- Sau sửa: cue `…?! --` không gọi provider, có `skip_tts=true`, `tts_skip_reason=no_speakable_text`; voice track im lặng được tạo đủ 1,000 giây.
- Unicode guard PASS với tiếng Việt, chữ Hán và punctuation-only.
- Nhóm ảnh hưởng chạy trong image dự án: 37/37 PASS.

## Retest chế độ OCR + ASR độc lập

- Contract/UI: lựa chọn `OCR + ASR độc lập — video đa ngôn ngữ` được validate; chỉ bật với caption source Trung.
- Fusion: caption `这个世界上怎么会` overlap lời Hàn được giữ làm source dịch; ASR Hàn overlap bị loại, cue Hàn không overlap vẫn được giữ với `source_language=ko`.
- Translation: nhóm `zh-CN` và `ko` dùng translator theo source riêng, kết quả ghép lại giữ đúng timeline.
- Regression ảnh hưởng: 63/63 PASS trong image dự án.
- Full backend: 422 tests, 420 PASS, 1 FAIL và 1 ERROR đã tồn tại ngoài phạm vi (`test_social_publishing` nhận 422; thiếu `infrastructure.douyin_qr_auth`).
- Chưa chạy provider/OCR/model thật trên video người dùng; real-video E2E vẫn NOT_RUN và cần OCR được bật trong runtime.

## Retest hủy job tức thời

- Job queued và processing đều trả `status=cancelled` ngay trong transaction hủy; không còn trạng thái trung gian `cancelling` cho request mới.
- Update `completed/progress=1.0` giả lập từ worker đến muộn bị bỏ qua; job giữ `cancelled`.
- Tenant isolation, job event và frontend regression: 43/43 PASS.
- Full backend: 423 tests, 421 PASS; 1 FAIL social publishing và 1 ERROR thiếu Douyin QR module là lỗi nền ngoài phạm vi.
- Chưa đo thời gian giải phóng CPU của native Whisper/FFmpeg đang chạy; đây là cooperative cancellation, không phải process kill cưỡng bức.

## Retest OCR Linux ARM — 2026-10-19

- `tesseract --list-langs` trong image mới: `chi_sim`, `eng`, `osd`.
- Config `auto` trong container Linux ARM chọn `tesseract`; không import/khởi tạo PaddleOCR.
- Unit/regression config + OCR + domain + checkpoint: 38/38 PASS.
- Smoke gọi Tesseract trên frame video/ảnh tham chiếu chạy hoàn tất, không làm server crash; chất lượng frame đơn thấp nên chưa dùng làm kết luận accuracy.
- Container triển khai với PostgreSQL healthy; Uvicorn startup complete và `/docs` trả HTTP 200 từ trong container.
