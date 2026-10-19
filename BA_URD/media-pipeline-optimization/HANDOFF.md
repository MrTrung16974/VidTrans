# Handoff — Tối ưu pipeline nhận diện, dịch và ghép video

- Feature: `media-pipeline-optimization`
- Workspace: VidTrans
- Cập nhật: 2026-10-18
- Giai đoạn: subtitle segmentation + audio duration đã triển khai; regression PASS; real-video rerun và checkpoint SIGKILL còn mở
- Plan: version `0.1`, `APPROVED` bởi người dùng ngày 2026-10-16 với nội dung “bắt đầu làm”

## Mục tiêu và phạm vi

Tối ưu hiệu năng, chất lượng, checkpoint/retry và khả năng vận hành của pipeline từ media local qua ASR/OCR, translation, TTS/audio đến render. Không gồm tải nguồn, đăng mạng xã hội, thêm ngôn ngữ hoặc deploy production trong giai đoạn hiện tại.

## File cần đọc theo thứ tự

1. `BA_URD/media-pipeline-optimization/ANALYSIS.md`
2. `BA_URD/media-pipeline-optimization/PLAN.md`
3. `ARCHITECTURE.md`
4. `BA_URD/media-pipeline-optimization/URD.md`
5. `DEV/media-pipeline-optimization/DESIGN.md`
6. `DEV/media-pipeline-optimization/IMPLEMENTATION.md`

## Đã làm

- Hoàn tất analysis/plan, ghi nhận approval plan 0.1.
- Tạo URD, open questions, review, traceability, design, implementation và testcase/report Lát A.
- Thêm stage timing/RTF, atomic JSON, media probe tổng hợp và TTS future error propagation.
- Thêm manifest/fingerprint/checksum checkpoint per-job cho transcript, bản dịch hoàn chỉnh, TTS bundle, mixed audio và rendered video; retry không reuse artifact lỗi/corrupt.
- Render checkpoint được materialize atomically ra output; TTS state dùng relative path và không ghi đè layout hiện tại.
- Source thay đổi nằm tại `backend/main.py`, `backend/pipeline/{stage_metrics,parallel,checkpoint_payloads}.py`, `backend/infrastructure/{artifact_io,media_probe,stage_checkpoint}.py` cùng unit tests mới.

## Quyết định và việc chờ review

- Lát A dùng metric không chứa nội dung, cache mặc định per-job, chưa bật Fast/hardware encoder.
- Q01–Q05 vẫn OPEN; Q04/Q05 chặn benchmark và kết luận SLO, Q02/Q03 chặn profile/cross-job cache.
- Kill/restart đã chạy lại trên tenant namespace; adaptive ASR/OCR và cross-job cache vẫn chờ quyết định liên quan.

## Kiểm chứng đã chạy

- Unit/feature/regression chọn lọc mới nhất: 159/159 PASS.
- AST parse, import main, media probe local và `git diff --check`: PASS.
- Live provider/media smoke: PASS với Google/MyMemory chain, gTTS, FFmpeg và Pillow fallback; output H.264/AAC 640×360, 4 giây.
- Host mặc định thiếu Pillow/edge-tts và FFmpeg thiếu subtitles/drawtext; Pillow được cài trong virtualenv tạm để xác minh fallback. Docker chưa chạy.
- Kill/restart checkpoint retest: FAIL vì transcript/translation hit nhưng TTS miss; fingerprint TTS trước/sau giống nhau.
- Media duration integration: PASS; source 2 giây + voice 0,5 giây tạo mixed audio 2 giây. Kết quả cũ 12→3,296 giây là baseline trước sửa.
- Benchmark/quality corpus: BLOCKED; không suy ra từ unit/live smoke.

## Ràng buộc

- Không đọc/sửa `.env` hay ghi secret vào tài liệu/log.
- Không log transcript hoặc nội dung bản dịch trong metric.
- Cross-job cache phải chờ policy tenant/TTL/delete được duyệt.
- Không đánh dấu PASS hoặc đạt SLO trước khi có benchmark trên cùng corpus/hardware.

## Bước tiếp theo

Điều tra validator/dependency làm TTS cache miss sau SIGKILL, rồi chạy lại recovery và hai video benchmark trên bản sao. Trước khi chốt SLO hoặc Lát C–E cần giải quyết Q02–Q05.

## Bổ sung 2026-10-18

- Đã sửa `DF_001`: pad/trim voice/mixed audio theo source duration; integration tổng hợp PASS.
- Đã thêm phân đoạn cue dựa trên word timestamp/dấu câu/pause, cue ID ổn định và final flush; không chia timing giả cho cue thiếu word timestamp.
- Regression mở rộng 159/159 PASS.
- Baseline hai job hiện có đã ghi trong `TEST/.../BASELINE.md`; chưa chạy đè artifact người dùng. `27099424` không phải corpus Trung→Việt hợp lệ dù request ghi `zh-CN`; `c863fbc1` là nội dung Hán và cue cuối phủ tới 783,300/783,370s.
- TC_030 đang FAIL: sau SIGKILL, TTS fingerprint trước/sau giống nhau nhưng checkpoint bị miss. Không được tiếp tục ghi recovery PASS cho build hiện tại.
- Bước kế tiếp: tìm nguyên nhân validator/dependency của TTS miss, sau đó chạy lại hai video trên bản sao/job benchmark có sự cho phép gọi provider; thu stage metrics trước/sau ít nhất 3 lần nếu cần kết luận SLO.

## Bổ sung từ video `output_3c10ceec.mp4`

- Đã tái hiện trực quan cue Việt dài bốn dòng kéo qua nhiều câu Trung trong video 26,603 giây.
- Đã sửa timeline SRT/ASS bám source `start/end`, không bám `tts_start/tts_end`.
- Mặc định cue mới: preferred 14, max 20 ký tự nguồn, tối đa 3,2 giây theo word timestamp; cue thiếu timestamp vẫn không bị chia thời gian giả.
- Đã sửa `wrap_text` không làm mất từ sau dòng thứ hai.
- Regression mới nhất 162/162 PASS.
- Không có source/job `3c10ceec` trong workspace nên chưa rerender chính video; cần video nguồn hoặc retry job trên hệ thống đã tạo output đó.

## Bổ sung audio/màu vietsub

- Voice mix gain mặc định 1,10; tiếng gốc mặc định 0,50 và giữ tiếng gốc bật.
- UI có color picker chữ/nền, opacity và checkbox nền hộp; mặc định chữ trắng trên hộp đen 82% theo ảnh tham chiếu.
- ASS, drawtext và Pillow fallback cùng sử dụng style màu đã validate.
- Frontend cache version `20261018-11`; nhóm test ảnh hưởng 15/15 PASS.
- Cần restart/rebuild backend và hard refresh frontend trước khi tạo job mới.
- Follow-up UX: đã bỏ ô nhập opacity; dùng cố định default 0,82 và cache frontend `20261018-12`; static tests 9/9 PASS.

Prompt tiếp tục: `Dùng $feature-delivery-workflow. Đọc BA_URD/media-pipeline-optimization/HANDOFF.md và các file được dẫn; kiểm tra trạng thái hiện tại, tiếp tục bước đã được duyệt, không lặp lại phần đã hoàn tất.`

## Bổ sung lỗi TTS text rỗng

- Đã chặn cue chỉ có dấu câu/ký hiệu trước Edge TTS/gTTS bằng Unicode letter/number guard.
- Cue không đọc được có `skip_tts=true`, `tts_skip_reason=no_speakable_text`; nếu không còn cue voice, pipeline tạo silent track đúng duration thay vì fail.
- TTS fingerprint version tăng lên 4; nhóm ảnh hưởng 37/37 PASS.
- Cần rebuild/restart service trước khi retry job lỗi.

## Bổ sung chế độ OCR + ASR độc lập

- Người dùng đã duyệt bắt đầu phát triển và yêu cầu đây là lựa chọn opt-in.
- Đã thêm `subtitle_source=independent`: OCR Trung là source ưu tiên; ASR tự detect ngôn ngữ audio, không so sánh xuyên ngôn ngữ; fallback cue mang source language riêng.
- Translation dịch theo nhóm ngôn ngữ từng cue và giữ nguyên timeline. Runtime thiếu OCR sẽ fail-fast, không âm thầm dùng speech-only.
- Cache version: transcript 4, OCR/ASR merge 2, translation 3, frontend `20261018-14`.
- Test ảnh hưởng 63/63 PASS; full backend 420/422 PASS với 2 lỗi nền ngoài phạm vi.
- Chưa deploy/restart và chưa chạy E2E video thật. Bước tiếp theo: build image, bật/kiểm tra PaddleOCR trên runtime, rồi chạy video mẫu với lựa chọn mới.

## Bổ sung hủy job tức thời

- Job chưa terminal chuyển thẳng sang `cancelled` khi API nhận yêu cầu; UI không còn chờ “Đang hủy”.
- Store và `update_job` chặn update worker đến muộn ghi đè terminal state. Hủy lặp lại vẫn idempotent và tenant check giữ nguyên.
- Frontend cache `20261018-15`; nhóm ảnh hưởng 43/43 PASS, full backend 421/423 PASS với hai lỗi nền cũ.
- Cancellation vẫn cooperative: native Whisper/FFmpeg đang giữa call có thể cần tới callback/checkpoint kế tiếp mới giải phóng CPU, nhưng UI/job/artifact không chờ và không thể hoàn tất lại.

## Bổ sung OCR Linux ARM 2026-10-19

- Đã thay lỗi fail-fast PaddleOCR bằng backend Tesseract ổn định: `VIDTRANS_OCR_BACKEND=auto` tự chọn `tesseract` trên Linux ARM; có thể ép `paddle`, `tesseract` hoặc `off`.
- Dockerfile cài Tesseract cùng model `chi_sim`; OCR TSV được chuẩn hóa về `OCRLine`, fingerprint ghi backend.
- 38/38 test liên quan PASS; image runtime có `chi_sim`, config chọn `tesseract`, Uvicorn startup hoàn tất và `/docs` trả 200.
- Do proxy TLS chặn tải lại PyTorch, máy local hiện triển khai bằng image overlay từ image sẵn có; Dockerfile chuẩn đã cập nhật để CI/VPS build sạch. File overlay tạm không thuộc source bàn giao.
- Việc tiếp theo: retry job video bằng lựa chọn `OCR + ASR độc lập`; thu output thật để đánh giá accuracy và tinh chỉnh crop/preprocess nếu cần.
