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

## Khớp cao độ và làm mượt (2026-10-18)

- **Dò F0 bằng YIN** (CMNDF + nội suy parabol) thay cho autocorrelation argmax; audio phân tích được lọc `highpass=65,lowpass=1500` để bỏ tiếng ù/bass.
- **Ngưỡng năng lượng theo nền:** frame phải vượt cả `0.35 × p90` lẫn `1.6 × p20` (mức nền nhạc ở khoảng nghỉ), giới hạn trên `0.5 × p90`.
- **Chọn cụm cao độ nặng nhất** (trọng số = RMS × độ tuần hoàn, cửa sổ ±4 cung) thay vì trung vị toàn bộ, để frame nhạc nền đông hơn không loại mất giọng nói.
- **Độ tin cậy** = độ phủ (frame có thanh / frame đủ to, so với 60%) × độ tuần hoàn × độ ổn định (MAD theo cung, tối đa 6). Cue dưới `MIN_CONFIDENCE` nhưng ≥ 0.2 và nằm rõ một vùng cao độ dùng `weak_pitch` trước khi theo giọng chung của video.
- **Khớp cao độ TTS:** cue cùng giọng liên tiếp tạo thành một lượt nói (tách khi lệch > 4 cung); cả lượt dùng chung trung vị có trọng số → `voice_pitch_offset_hz` = 50% khoảng cách (theo cung) tới F0 gốc của giọng Edge (NamMinh 123 Hz, HoaiMy 206 Hz đo thực tế), tối đa ±2.5 cung, làm tròn 2 Hz. Edge áp pitch ngay khi tổng hợp nên formant vẫn tự nhiên.
- **Ghép track:** mỗi clip được đưa về −17 dBFS (đo phần có tiếng, giới hạn ±6 dB) và có fade 12 ms vào / 40 ms ra để hết tiếng tách.
- **Edge TTS** thử lại tối đa 4 lần trước khi rơi về gTTS (gTTS không có giọng nam/nữ hay pitch). Yêu cầu `edge-tts>=7.3.1`; bản 7.2.8 bị Bing trả 403.
- Giới hạn: phân loại chỉ dựa trên cao độ nên giọng nam cao (~200 Hz) hoặc nhạc nền to gần bằng giọng (SNR ≤ 5 dB) vẫn có thể nhầm.
