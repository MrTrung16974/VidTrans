# Kế hoạch — Tối ưu khớp giọng tự động

1. Cập nhật thuật toán pitch: lọc outlier và đo độ ổn định.
2. Phân tích hai lượt trên toàn bộ cue, bổ sung global/neighbor/smoothing fallback.
3. Giữ contract `voice_type` hiện tại; mở rộng `voice_routing` để quan sát quyết định.
4. Bổ sung unit test cho im lặng, outlier và chuyển người nói.
5. Chạy test voice routing, timing và kiểm tra syntax/diff.
6. Tối ưu cửa sổ TTS theo khoảng nghỉ thật và bổ sung metadata chất lượng đồng bộ.
7. Bổ sung catalog preset giọng, UI lựa chọn, validation backend và lưu/resume xuyên pipeline.

Rủi ro chính: smoothing quá mạnh có thể làm mất chuyển người nói thật; giải pháp là chỉ sửa điểm đơn lẻ có confidence thấp và giữ các chuyển đổi có bằng chứng mạnh.
