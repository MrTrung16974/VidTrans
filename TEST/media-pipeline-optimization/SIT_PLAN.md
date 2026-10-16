# SIT plan — Provider/media và kill/restart

## Mục tiêu

Tách hai nguồn rủi ro để lỗi có thể chẩn đoán được:

1. Live media smoke: provider dịch thật, TTS thật, FFmpeg burn/mux và ffprobe output.
2. Crash recovery: process riêng, persistent SQLite/work directory cô lập, kill cưỡng bức tại ranh giới stage rồi restart cùng job.

## Nhánh 1 — Provider/TTS/FFmpeg thật

- Dữ liệu: một câu tiếng Trung tổng hợp, video test pattern 4 giây.
- External side effect: hai request read-only tới provider dịch/TTS công cộng; không credential, không publish.
- PASS: bản dịch không rỗng; TTS tạo audio; FFmpeg tạo MP4 có cả video/audio; subtitle filter chạy thành công.
- Không chứng minh: ASR/OCR, persistence, resume, timing chất lượng trên video thật.

## Nhánh 2 — Kill/restart end-to-end

- Chạy trong bản sao backend và SQLite/work/output tạm để không tác động job hiện hữu.
- Dùng source tổng hợp và checkpoint transcript/translation được tạo theo đúng fingerprint để loại biến động ASR/provider khỏi phép thử recovery.
- Process 1 chạy mode lồng tiếng; parent theo dõi manifest và gửi `SIGKILL` sau khi TTS hoặc audio checkpoint commit.
- Process 2 gọi recovery/requeue và chạy lại persisted `resume_request`.
- PASS: job completed; stage trước điểm kill là cache hit; output MP4 hợp lệ; source/checkpoint vẫn tồn tại; không có output thành công bị ghi dở.
- Nhánh này cần harness process cô lập trước khi chạy; không dùng database `backend/work/jobs.sqlite3` hiện tại.

## Thứ tự

Chạy live media smoke trước. Chỉ chạy crash recovery sau khi media smoke PASS, để lỗi mạng/provider không bị nhầm với lỗi resume.

## Kết quả ngày 2026-10-16

- Live media smoke: `PASS` sau khi bổ sung Pillow vào virtualenv tạm.
- Provider dịch: thành công, 2,995 giây cho một câu tổng hợp.
- gTTS: thành công, 0,369 giây.
- Render/mux: thành công qua Pillow fallback, 3,890 giây.
- Output: H.264 + AAC, 640×360, 4 giây.
- Capability host: FFmpeg có libx264 nhưng thiếu `subtitles` và `drawtext`; Python hệ thống thiếu Pillow và `edge-tts`.
- Kill/restart recovery: `PASS`; process -9 sau TTS commit, restart_count=1, cache hit transcript/translation/TTS.
- Media correctness sau restart: `FAIL`; source 12 giây, output 3,296 giây, delta 8,704 giây do audio không pad và render dùng `-shortest`.
