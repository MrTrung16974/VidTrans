# Baseline — Tối ưu pipeline media

- Trạng thái: `BLOCKED` đối với baseline end-to-end.
- Ngày: 2026-10-16

## Đã xác minh

- Corpus local hiện có 5 video H.264, 1920×1080, 30 fps.
- Bốn file dài khoảng 643,612 giây; một file dài khoảng 783,370 giây.
- Media probe mới đọc đúng một file mẫu: 1920×1080, 643,6 giây, 30 fps, có audio.

## Chưa chạy

- ASR/OCR/translation/TTS/render end-to-end.
- Cold/warm cache, retry theo stage và batch concurrency.
- WER/CER, translation reference/human rubric, TTS drift và render quality.
- CPU/RSS/temp disk, p50/p95 và RTF theo corpus.

## Blocker

- Q04: chưa xác nhận corpus được phép dùng và ground truth.
- Q05: chưa có profile hardware/container production.
- Provider bên ngoài biến động; cần ghi condition và chạy lặp ≥3 lần/case.

Không dùng metadata video hiện có để tuyên bố đạt NFR_01–04.
