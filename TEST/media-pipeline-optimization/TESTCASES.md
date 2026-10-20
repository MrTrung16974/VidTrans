# Test cases — Tối ưu pipeline media, Lát A–B2

- Feature: `media-pipeline-optimization`
- URD: 0.1
- Phạm vi hiện tại: Lát A
- Dữ liệu: payload/file tổng hợp; không provider/model thật.

| TC ID | UC/BR/AC | Nhóm/ưu tiên | Tiền điều kiện | Dữ liệu | Bước thực hiện | Kết quả mong đợi | Trạng thái | Bằng chứng |
|---|---|---|---|---|---|---|---|---|
| TC_001 | UC_01/BR01_01/AC01_01 | Unit/P1 | Timer callback hợp lệ | duration video 10s | Chạy stage thành công | completed, duration >=0, RTF hợp lệ | PASS | `test_stage_metrics.py` |
| TC_002 | UC_01/BR01_02/AC01_02 | Unit/P0 | Stage ném lỗi | synthetic RuntimeError | Thoát context | metric failed, exact exception được propagate | PASS | `test_stage_metrics.py` |
| TC_003 | UC_01/EF01_01 | Unit/P1 | Callback metric lỗi | callback RuntimeError | Stage thành công/thất bại | Lỗi callback không mask pipeline exception | PASS | `test_stage_metrics.py` |
| TC_004 | UC_03/BR03_04/AC03_01 | Unit/P0 | Một future lỗi | 3 futures tổng hợp | Thu kết quả | Hủy pending khi có thể, propagate exact lỗi | PASS | `test_parallel.py` |
| TC_005 | UC_03/BR03_04 | Unit/P1 | Mọi future thành công | index đảo thứ tự | Thu kết quả | Kết quả trả đúng index | PASS | `test_parallel.py` |
| TC_006 | UC_02/BR02_04/AC02_03 | Unit/P0 | Directory tạm | JSON Unicode | Atomic write | Đích là JSON đầy đủ, không còn temp | PASS | `test_artifact_io.py` |
| TC_007 | UC_02/BR02_04 | Unit/P0 | Đích cũ tồn tại | Mock replace lỗi | Atomic write | Đích cũ không bị ghi dở, temp được dọn | PASS | `test_artifact_io.py` |
| TC_008 | UC_04/BR04_01/AC04_01 | Unit/P1 | ffprobe runner mock | video+audio JSON | Probe | Parse duration/dimensions/fps/audio đúng | PASS | `test_media_probe.py` |
| TC_009 | UC_04/BR04_01 | Unit/P1 | ffprobe runner mock | video không audio | Probe | `has_audio=false`, metadata video đúng | PASS | `test_media_probe.py` |
| TC_010 | UC_04/BR04_01 | Unit/P1 | ffprobe runner mock | không video/JSON lỗi | Probe | Ném MediaProbeError sanitize | PASS | `test_media_probe.py` |
| TC_011 | NFR_07 | Regression/P1 | Code sau sửa | Unit suite chọn lọc | Chạy tests | ASR/translation/TTS timing/media validation không regression | PASS | Suite tổng hợp 137 tests |
| TC_012 | NFR_01-04 | Benchmark/P1 | Golden corpus + production-like host | Chưa có | Chạy baseline trước/sau | Có median/p95/quality report | BLOCKED | Q04/Q05 |
| TC_013 | UC_02/BR02_01-03/AC02_01 | Unit/P0 | Work dir tạm | Fingerprint + transcript payload | Commit rồi load | Checksum/fingerprint/schema khớp, payload được reuse | PASS | `test_stage_checkpoint.py` |
| TC_014 | UC_02/BR02_02/AC02_02 | Unit/P0 | Checkpoint đã có | Fingerprint khác/artifact corrupt | Load | Cache miss, không trả artifact sai | PASS | `test_stage_checkpoint.py` |
| TC_015 | UC_02/BR02_04/AC02_03 | Unit/P0 | Manifest corrupt/version lạ | Synthetic JSON | Load | Cache miss an toàn | PASS | `test_stage_checkpoint.py` |
| TC_016 | UC_02/NFR_05 | Security/P0 | Work dir tạm | `../outside.json` | Commit | Từ chối path traversal | PASS | `test_stage_checkpoint.py` |
| TC_017 | UC_02/AC02_01 | Unit/P0 | Checkpoint hợp lệ | Compute callback có counter | Load-or-compute hai lần | Lần hai không gọi compute | PASS | `test_stage_checkpoint.py` |
| TC_018 | UC_02/BR02_03 | Unit/P0 | Translation payload | complete/fallback/skipped-only | Validate | Chỉ complete active segments được cache | PASS | `test_checkpoint_payloads.py` |
| TC_019 | UC_02/BR02_01-03 | Unit/P0 | TTS bundle đã commit | WAV dependency thiếu/corrupt | Load | Cache miss; bundle checksum không được reuse | PASS | `test_stage_checkpoint.py` |
| TC_020 | UC_02/NFR_05 | Security/P0 | Work dir tạm | Dependency `../outside.wav` | Commit | Từ chối path traversal | PASS | `test_stage_checkpoint.py` |
| TC_021 | UC_02/BR02_04 | Unit/P0 | TTS state + layout hiện tại | Relative cue/voice paths | Serialize/restore | Khôi phục TTS timing/Path, không ghi đè layout | PASS | `test_checkpoint_payloads.py` |
| TC_022 | UC_02/BR02_04 | Unit/P0 | Binary source + output cũ | Copy/replace lỗi mock | Materialize render | Copy đủ byte; lỗi giữ output cũ và dọn temp | PASS | `test_artifact_io.py` |
| TC_023 | UC_03/UC_04/NFR_06 | SIT/P0 | Network được phép; Pillow trong temp venv | Câu Trung + video test pattern 4 giây | Dịch thật → gTTS thật → burn/mux → ffprobe | MP4 H.264/AAC 640×360, duration 4 giây | PASS | `manual_real_media_smoke.py`; output temp `vidtrans-real-smoke-vrzeoqh4` |
| TC_024 | UC_02/AC02_01-03 | Integration/P0 | Backend/SQLite/work/output cô lập | Kill sau TTS checkpoint | Requeue/resume cùng job sau `SIGKILL` | Transcript/translation/TTS hit; audio/render chạy lại; job completed | PASS | `manual_kill_restart_e2e.py`; process -9, restart_count=1 |
| TC_025 | NFR_03/NFR_04 | Integration/P0 | Output từ TC_024 | Source 12 giây | ffprobe source/output | Duration delta ≤0,25 giây; A/V output không bị truncate | FAIL | Output 3,296 giây; delta 8,704 giây |
| TC_026 | Đồng bộ cue | Unit/P0 | ASR có word timestamp | Dấu câu, pause và cụm cuối | Phân đoạn | Cue dùng timestamp từ word, ID duy nhất, cụm cuối không mất | PASS | `test_subtitle_segmentation.py` |
| TC_027 | Đồng bộ cue | Unit/P0 | Cue không có word timestamp | OCR/legacy cue dài | Phân đoạn | Giữ nguyên timing; không chia đều giả tạo | PASS | `test_subtitle_segmentation.py` |
| TC_028 | NFR_04 | Integration/P0 | Source 2s, voice 0,5s | FFmpeg local | Mix audio | WAV output 2s ±0,05s | PASS | `test_audio_duration.py` |
| TC_029 | Regression | Unit/P1 | Code 2026-10-18 | 23 module test liên quan | Chạy suite | Không regression ASR/dịch/layout/checkpoint/job | PASS | 159/159 tests |
| TC_030 | UC_02/NFR_06 | Integration/P0 | Harness tenant namespace | Kill sau TTS bundle ổn định | Restart | TTS checkpoint hit | FAIL | Fingerprint trước/sau giống nhau nhưng `stage_cache.tts=miss` |
| TC_031 | Đồng bộ hiển thị | Unit/P0 | Cue có source timing và TTS timing lệch | start 1–2s, TTS 4–6s | Ghi SRT | SRT dùng 1–2s | PASS | `test_audio_duration.py::test_srt_uses_source_timing_not_delayed_tts_timing` |
| TC_032 | Không mất nội dung | Unit/P0 | Văn bản cần hơn hai dòng theo width cũ | Chuỗi tiếng Việt tổng hợp | Wrap | Tối đa hai dòng và giữ đủ từ | PASS | `test_audio_duration.py::test_wrap_text_keeps_every_word` |
| TC_033 | Cue liên tục | Unit/P0 | Lời nói không dấu câu/không pause | Word timestamp dài 5,8s | Phân đoạn | Mỗi cue không vượt khoảng 3,2s | PASS | `test_subtitle_segmentation.py::test_splits_continuous_speech_at_max_timed_duration` |
| TC_034 | Audio mix | Unit/P1 | Default lồng tiếng | Constants | Kiểm tra cấu hình | Voice 1,30; tiếng gốc 0,30 | PASS | `test_audio_duration.py::test_balanced_default_voice_and_original_audio_levels` |
| TC_035 | Màu vietsub | Unit/P1 | CSS colors + opacity | Vàng/đen | Normalize ASS | Màu BGR/alpha đúng, BorderStyle 3 | PASS | `test_audio_duration.py::test_colored_box_style_is_normalized_for_ass` |
| TC_036 | Render vietsub | Unit/P1 | Hộp màu bật | ASS synthetic | Ghi ASS | Style chứa màu chữ và opaque box | PASS | `test_audio_duration.py::test_ass_writes_opaque_box_style_from_css_colors` |
| TC_037 | TTS input guard | Unit/P0 | Cue dịch chỉ có dấu câu | `…?! --` | Tổng hợp TTS và dựng voice track | Không gọi provider; đánh dấu skip; silent track dài đúng 1s | PASS | `test_audio_duration.py::test_tts_skips_punctuation_only_cue_before_calling_provider` |
| TC_038 | Multimodal/P0 | Caption Trung overlap lời Hàn | OCR Trung + ASR Hàn | Hợp nhất độc lập | Chỉ giữ OCR ở đoạn overlap; không so text xuyên ngôn ngữ | PASS | `test_ocr_pipeline.py::test_independent_merge_keeps_chinese_caption_over_korean_speech` |
| TC_039 | Translation/P0 | Cue Trung/Hàn xen kẽ | 3 cue, 2 source language | Dịch theo nhóm rồi ghép | Translator đúng từng source; timeline 0–1–2 giữ nguyên | PASS | `test_translation_pipeline.py::test_translates_each_source_language_and_restores_timeline_order` |
| TC_040 | Contract/P1 | Form nguồn Trung | `subtitle_source=independent` | Validate request/UI | Enum được nhận; ngôn ngữ caption khác Trung bị từ chối | PASS | `test_domain_models.py`, `test_frontend_static.py` |
| TC_041 | Job lifecycle/P0 | Job đang processing | Gửi cancel | Hủy một lần | API/store trả `cancelled` ngay, có `finished_at` | PASS | `test_job_store.py::test_cancel_queued_and_running_jobs` |
| TC_042 | Concurrency/P0 | Worker còn chạy sau cancel | Update completed đến muộn | Ghi update | Trạng thái/progress cancelled không bị ghi đè | PASS | `test_job_store.py::test_cancelled_job_ignores_late_worker_updates` |
| TC_043 | OCR runtime/P0 | Linux ARM, PaddleOCR không an toàn | `VIDTRANS_OCR_BACKEND=auto` | Load config và OCR TSV | Chọn Tesseract; gom đúng dòng/chữ/confidence/bbox | PASS | `test_app_config.py`, `test_ocr_pipeline.py`; image có `chi_sim` |
