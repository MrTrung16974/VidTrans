# Phân tích — job 05a05e62: dịch lệch, phụ đề lệch giọng gốc, mất chữ

- Nguồn: `~/Downloads/05a05e62.artifacts/05a05e62.translation.json`, `output_05a05e62.mp4` (306s, có chữ Trung cứng).
- Job chạy 23/10/2026 16:27 — trước 2 bản sửa ASR cùng ngày (nghe lại khoảng trống VAD, chia từ theo đuôi phát âm).
- Cấu hình: `source_method=speech`, dịch `gemini-3.5-flash-lite` + Google dự phòng, 129 cue.

## Lỗi 1 — bản dịch lệch 1 dòng (nguyên nhân chính "phụ đề lệch giọng")

- Cue 19–37 (0:39–1:24): mỗi câu gốc mang bản dịch của câu kế tiếp. Ví dụ `肯定是亲生的` → "Hai mẹ con này ra rồi kìa" (của `这一个母子可出来了`).
- Cơ chế: trong một lô Gemini, model bỏ cue 19 (lời bài hát), dồn bản dịch lên một ô và lặp câu cuối (cue 37 và 38 cùng "Hạo Hạo, ông nội ơi") để đủ số lượng.
- `parse_translations` chỉ kiểm tra số lượng và ID nên chấp nhận kết quả lệch.

## Lỗi 2 — chất lượng tụt giữa video

- Cue 39–79 có văn phong Google Translate ("bạn", "Bí ẩn không được kiểm soát", "Tôi hơi ngu dốt").
- `FallbackTranslator` chuyển cả lô sang Google ngay khi Gemini lỗi một lô (JSON sai, sai số lượng); Gemini không chia nhỏ lô để thử lại như Qwen3. Người dùng không được báo.

## Lỗi 3 — mất chữ cả đoạn

- Khoảng trống trong bản dịch: 34–39s, 137–145s, 184–189s, **190–235s**, 247–251s.
- Chạy lại ASR bằng code hiện tại vẫn mất 190–235s: Whisper rơi vào vòng lặp ảo giác (`condition_on_previous_text=True`) — "我死定了" lặp nhiều lần, một segment dài 37s (195–232s), "媽媽" lặp 10 lần. Vì thời gian đã bị segment rác phủ kín nên bản sửa "nghe lại khoảng trống" không kích hoạt.

## Lỗi 4 — nghe sai và cắt từ (giới hạn của chế độ chỉ nghe)

- `亲自建计` (đúng: 亲子鉴定), `是私小太臭`, `迷史没有控制住`, câu cắt giữa từ (`你无` / `所谓`, `了` / `怎么` / `回` / `事`) → bản dịch vô nghĩa ("Bí ẩn không được kiểm soát", "trở về", "nhiều thứ").
- Video có chữ Trung cứng suốt; đọc chữ trên hình (OCR) cho câu gốc chuẩn hơn hẳn.
