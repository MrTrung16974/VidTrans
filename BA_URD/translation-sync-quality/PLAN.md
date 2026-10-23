# Kế hoạch sửa

- Feature: `translation-sync-quality`
- Trạng thái: `DRAFT — chờ duyệt`

| # | Ưu tiên | Việc làm | Kiểm chứng |
|---|---|---|---|
| 1 | P1 | **Chống lệch dòng khi dịch theo lô (Gemini, Qwen3):** yêu cầu model chép lại câu gốc trong mỗi item (`{"id","source","text"}`), so khớp với câu gửi đi; bắt cả trường hợp 2 câu gốc khác nhau ra bản dịch giống hệt nhau liền kề. Sai thì thử lại với lô nhỏ hơn (chia đôi), cuối cùng dịch từng câu. | Test tái hiện đúng lô cue 19–38 của job này: phải phát hiện lệch và tự sửa. |
| 2 | P1 | **Không âm thầm tụt sang Google:** Gemini lỗi một lô → chia nhỏ và thử lại trước khi dùng Google; ghi provider theo từng cue; job hiển thị cảnh báo "N dòng dịch bằng Google do Gemini lỗi". | Test: lô Gemini lỗi JSON một lần → không dùng Google; lỗi liên tục → có cảnh báo và đếm đúng. |
| 3 | P1 | **Phát hiện vòng lặp ảo giác ASR:** segment lặp cùng chữ ≥3 lần liên tiếp, hoặc kéo dài bất thường so với số chữ (vd. 37s cho 4 chữ) → bỏ, coi là khoảng trống, nghe lại không VAD và `condition_on_previous_text=False`. Gộp chung với cơ chế nghe lại khoảng trống VAD. | Chạy trên audio job này: 190–235s và 155–182s phải có lời thoại; chạy lại video cf37cf06 không thoái lui. |
| 4 | P2 | **Dùng chữ cứng làm câu gốc khi có:** bước căn chữ đã phát hiện được dải chữ và thời điểm từng dòng; thêm OCR chỉ các khung đó (không quét cả video) để lấy câu gốc, Whisper chỉ dùng cho đoạn không có chữ. Cần quyết định: bật mặc định khi phát hiện chữ cứng, hay chỉ gợi ý. | So sánh trên job này và cf37cf06: tỉ lệ câu gốc đúng chữ trên hình, thời gian xử lý tăng thêm. |
| 5 | P3 | Lời bài hát/nhạc nền: đánh dấu cue nghi là lời hát (Whisper/OCR) để không lồng tiếng, vẫn hiện phụ đề. | Cue 19–21 của job này. |

## Thứ tự đề xuất

1 → 2 → 3 (sửa được ngay, không đổi trải nghiệm), sau đó 4 (cần chốt hành vi mặc định vì tăng thời gian xử lý), 5 tùy chọn.

## Rủi ro

- (1) tăng token đầu ra khoảng 30–40% do chép câu gốc; chi phí Gemini Flash Lite vẫn nhỏ.
- (3) ngưỡng phát hiện vòng lặp quá chặt có thể bỏ câu lặp thật ("别别别别"); chỉ bỏ khi lặp nguyên segment và thời lượng bất thường.
- (4) OCR (PaddleOCR) chậm trên CPU; chỉ chạy trên khung đã phát hiện để giới hạn chi phí.
