# Handoff — Tối ưu khớp giọng tự động

- Trạng thái: IMPLEMENTED_LOCAL.
- Source chính: `backend/pipeline/voice_routing.py`.
- Unit tests: `backend/tests/test_voice_routing.py`.
- Kết quả: 14 targeted tests PASS; sau tối ưu timing có 28 voice/timing/translation/domain regression tests PASS.
- Timing mới mượn tối đa 0,45 giây từ khoảng nghỉ thật, chừa transition gap và ghi metadata chất lượng khớp.
- UI/backend có 5 preset giọng allowlist; job cũ mặc định `natural`; regression mới nhất 30 PASS.
- Cần chạy smoke/UAT trong image cài đủ `requirements.txt`; runtime `uv` local hiện thiếu `edge_tts` nên chưa xác minh âm thanh preset thật.
- Tiếp theo: UAT với video thật gồm một người nói, hội thoại nam/nữ, âm thanh nhiễu và cue rất ngắn; dùng metadata `selection_source`/`confidence` để hiệu chỉnh ngưỡng nếu cần.
