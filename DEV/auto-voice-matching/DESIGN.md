# Thiết kế — Tối ưu khớp giọng tự động

## Thành phần

- `pipeline/voice_routing.py`: robust pitch statistics và bộ ổn định chuỗi quyết định.
- `pipeline/tts_timing.py`: tính cửa sổ đọc khả dụng và tempo thích ứng trong giới hạn tự nhiên.
- `domain/languages.py`: catalog preset giọng và tuning rate/pitch được allowlist.
- `tests/test_voice_routing.py`: dữ liệu WAV tổng hợp, không gọi mạng hoặc model ngoài.

## Thuật toán

1. Phân tích tất cả cue và lưu raw result.
2. Lọc pitch frame bằng median/MAD để giảm octave/noise outlier.
3. Tính giọng chủ đạo bằng tổng trọng số confidence × thời lượng.
4. Cue ambiguous lấy hai hàng xóm cùng loại; nếu không có thì giọng chủ đạo, cuối cùng fallback.
5. Làm mượt điểm đảo đơn lẻ chỉ khi raw confidence dưới ngưỡng mạnh; chuyển đổi mạnh vẫn được giữ.

## Đồng bộ thời lượng

1. Cửa sổ đọc ưu tiên cue gốc, nhưng có thể mượn một phần khoảng nghỉ thật trước câu kế tiếp.
2. Chừa khoảng chuyển tiếp tối thiểu để hai câu không dính nhau.
3. Chỉ tăng tốc khi lệch vượt deadband; giới hạn tempo để giọng không bị gấp hoặc méo.
4. Ghi `tts_target_duration`, `tts_overrun` và `tts_fit_quality` để theo dõi chất lượng khớp.

## Preset giọng đọc

- `natural`: trung tính, không điều chỉnh.
- `warm`: chậm và trầm nhẹ.
- `bright`: cao và nhanh nhẹ.
- `deep`: trầm rõ, chậm vừa.
- `energetic`: nhanh và sáng hơn.
- Preset chỉ điều chỉnh rate/pitch trên Edge TTS; gTTS fallback giữ khả năng đọc nhưng không bảo đảm sắc thái tương đương.

## Compatibility và lỗi

- Giữ `route_segments_by_pitch(...)`, `voice_type`, summary hiện tại.
- Metadata bổ sung `raw_voice_type`, `resolved_voice_type`, `selection_source`.
- Audio không hợp lệ vẫn fail theo hành vi hiện tại để caller chuyển sang manual fallback.

## Test strategy

- Male/female rõ, silence neighbor fill, isolated weak flip smoothing, sustained speaker switch, metadata và summary.
- Cue vừa/thiếu thời lượng, khoảng nghỉ có thể mượn, khoảng nghỉ quá dài bị giới hạn và overrun metadata.
