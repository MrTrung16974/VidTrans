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

## Kiểm kê hai job hiện có ngày 2026-10-18

| Job/video | Source/output | Cue | Max duration cue | Last cue | Nhận xét |
|---|---:|---:|---:|---:|---|
| `27099424` | 62,501s / 62,501s | 2 | 29,630s | 47,220s | Request `zh-CN` nhưng transcript Latin; không hợp lệ cho benchmark Trung→Việt. |
| `c863fbc1` | 783,370s / 783,370s | 322 | 5,060s | 783,300s | Nội dung Hán; phần cuối được phủ sát duration. |

Stage metric có sẵn cho `27099424`: ASR 96,299s; translation 2,331s; render 3,465s. Job `c863fbc1` được tạo trước instrumentation nên không có stage metric. Chưa có lần chạy sau sửa trên cùng hai video, vì vậy không tính phần trăm cải thiện giả định.
