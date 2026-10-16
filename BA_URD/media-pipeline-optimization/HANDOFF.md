# Handoff — Tối ưu pipeline nhận diện, dịch và ghép video

- Feature: `media-pipeline-optimization`
- Workspace: VidTrans
- Cập nhật: 2026-10-16
- Giai đoạn: checkpoint recovery sau SIGKILL PASS; media-duration E2E FAIL; benchmark còn lại
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
- Kill/restart end-to-end cho TTS/audio/render chưa chạy; adaptive ASR/OCR và cross-job cache vẫn chờ quyết định liên quan.

## Kiểm chứng đã chạy

- Unit/feature/regression chọn lọc mới nhất: 137/137 PASS.
- AST parse, import main, media probe local và `git diff --check`: PASS.
- Live provider/media smoke: PASS với Google/MyMemory chain, gTTS, FFmpeg và Pillow fallback; output H.264/AAC 640×360, 4 giây.
- Host mặc định thiếu Pillow/edge-tts và FFmpeg thiếu subtitles/drawtext; Pillow được cài trong virtualenv tạm để xác minh fallback. Docker chưa chạy.
- Kill/restart checkpoint: PASS trong backend/SQLite/work/output tạm; process -9, restart_count=1, transcript/translation/TTS hit.
- Media correctness: FAIL; source 12 giây tạo output 3,296 giây do audio ngắn + render `-shortest` (`DF_001`).
- Benchmark/quality corpus: BLOCKED; không suy ra từ unit/live smoke.

## Ràng buộc

- Không đọc/sửa `.env` hay ghi secret vào tài liệu/log.
- Không log transcript hoặc nội dung bản dịch trong metric.
- Cross-job cache phải chờ policy tenant/TTL/delete được duyệt.
- Không đánh dấu PASS hoặc đạt SLO trước khi có benchmark trên cùng corpus/hardware.

## Bước tiếp theo

Sửa `DF_001` bằng contract pad/trim audio tới video duration và bump audio/render fingerprint version, rồi retest TC_024–025. Sau đó đo cold/warm retry; trước khi chốt SLO hoặc Lát C–E cần giải quyết Q02–Q05.

Prompt tiếp tục: `Dùng $feature-delivery-workflow. Đọc BA_URD/media-pipeline-optimization/HANDOFF.md và các file được dẫn; kiểm tra trạng thái hiện tại, tiếp tục bước đã được duyệt, không lặp lại phần đã hoàn tất.`
