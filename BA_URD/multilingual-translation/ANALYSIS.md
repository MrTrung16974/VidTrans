# Phân tích tính năng dịch đa ngôn ngữ

## 1. Thông tin chung

- Feature: `multilingual-translation`
- Trạng thái: `DRAFT`
- Nguồn yêu cầu: Người dùng yêu cầu tái cấu trúc hệ thống để dịch nhiều loại ngôn ngữ.
- Phạm vi phân tích: pipeline nhận diện giọng nói, dịch, phụ đề, lồng tiếng, API tạo job/batch, retry job và giao diện cấu hình.

## 2. Vấn đề hiện tại

### CONFIRMED

- `ProcessingRequest` chưa có `source_language` và `target_language`.
- ASR được thiết kế và đặt tên riêng cho tiếng Trung; hai lượt đầu ép Whisper dùng `zh`.
- translator cố định `zh-CN → vi`; fallback MyMemory cũng cố định cùng cặp ngôn ngữ.
- kiểm tra bản dịch coi mọi ký tự Hán trong đầu ra là lỗi, nên không dùng được cho đích tiếng Trung hoặc tiếng Nhật.
- metadata artifact đang ghi cứng `source_language=zh`, `target_language=vi`.
- TTS đang dùng hai giọng `vi-VN`; gTTS fallback cố định `lang=vi`.
- nội dung UI, trạng thái tiến trình và preview đều gắn với “Trung → Việt/Vietsub”.
- retry/resume lưu cấu hình xử lý nhưng chưa lưu cặp ngôn ngữ.
- OCR hiện tối ưu cho phụ đề chữ Trung và chưa có contract chọn model OCR theo ngôn ngữ.

### Hệ quả

- Thêm một ngôn ngữ bằng cách sửa hằng số sẽ làm logic dịch, xác thực, TTS và metadata lệch nhau.
- Job cũ không mang đủ thông tin để tái chạy an toàn nếu mặc định hệ thống thay đổi.
- Một số cặp ngôn ngữ hợp lệ sẽ bị báo “chưa dịch” do validation dựa trên chữ Hán thay vì ngôn ngữ đích.

## 3. Mục tiêu đo được

1. Người dùng chọn được ngôn ngữ nguồn (`auto` hoặc cụ thể) và ngôn ngữ đích khi tạo file đơn/batch.
2. Cặp ngôn ngữ được validate một lần tại domain và được giữ xuyên suốt create → queue → retry → artifact.
3. ASR nhận diện được ngôn ngữ nguồn đã chọn hoặc tự phát hiện bằng Whisper.
4. Provider dịch nhận cặp ngôn ngữ động và fallback độc lập vẫn hoạt động.
5. Kiểm tra đầu ra dựa trên cặp ngôn ngữ, không dựa trên giả định “đích luôn là tiếng Việt”.
6. Chế độ lồng tiếng chỉ cho phép ngôn ngữ có voice profile hợp lệ và chọn đúng Edge/gTTS locale.
7. Job cũ thiếu trường ngôn ngữ tiếp tục chạy như `zh → vi`.
8. Unit test bao phủ validation, ASR options, provider params, retry compatibility và TTS mapping.

## 4. TO-BE đề xuất

### Ngôn ngữ giai đoạn đầu

| Mã chuẩn nội bộ | Nhãn | Whisper source | Google/MyMemory | Edge TTS | gTTS |
|---|---|---:|---:|---:|---:|
| `vi` | Tiếng Việt | Có | Có | Có | Có |
| `en` | Tiếng Anh | Có | Có | Có | Có |
| `zh-CN` | Tiếng Trung giản thể | ánh xạ `zh` | Có | Có | ánh xạ `zh-CN` |
| `ja` | Tiếng Nhật | Có | Có | Có | Có |
| `ko` | Tiếng Hàn | Có | Có | Có | Có |
| `th` | Tiếng Thái | Có | Có | Có | Có |
| `id` | Tiếng Indonesia | Có | Có | Có | Có |
| `es` | Tiếng Tây Ban Nha | Có | Có | Có | Có |

Ngôn ngữ nguồn có thêm lựa chọn `auto`. Nguồn và đích không được trùng nhau sau khi nguồn đã được xác định.

### Kiến trúc

- Tạo catalog `LanguageProfile` làm nguồn duy nhất cho mã UI, mã Whisper, mã provider, locale TTS, voice nam/nữ và khả năng OCR.
- Đổi translator thành contract tổng quát nhận `source_language`, `target_language`; tên class không chứa “Vietnamese”.
- Tách validation bản dịch thành chiến lược theo ngôn ngữ: rỗng, giống nguồn, phản hồi lỗi provider và tỷ lệ script bất thường. Không cấm tuyệt đối chữ Hán.
- ASR trả thêm ngôn ngữ phát hiện và confidence; pipeline chốt source language trước khi dịch.
- TTS chọn voice qua catalog theo ngôn ngữ đích; job chỉ phụ đề không phụ thuộc voice availability.
- Metadata, tên trạng thái và TikTok content dùng ngôn ngữ đích của job.

## 5. Phạm vi

### Trong phạm vi

- Domain model và validation ngôn ngữ.
- API file đơn, batch, cấu hình lưu job và resume/retry.
- Whisper source cụ thể hoặc auto detect.
- Google + MyMemory với cặp ngôn ngữ động.
- Pipeline translation và artifact đa ngôn ngữ.
- Edge TTS/gTTS mapping cho danh sách giai đoạn đầu.
- UI chọn nguồn/đích, copy động, summary và trạng thái job.
- Migration tương thích bằng default tại thời điểm đọc job cũ; không yêu cầu sửa SQLite hiện hữu nếu config nằm trong JSON.
- Unit/integration tests cô lập, không gọi provider thật.

### Ngoài phạm vi giai đoạn đầu

- OCR đa script/model. OCR tiếp tục chỉ được bảo đảm cho tiếng Trung; các nguồn khác dùng Whisper.
- Dịch offline bằng model cục bộ.
- Tự động chọn dialect/giọng theo quốc gia.
- Thay đổi nghiệp vụ đăng TikTok ngoài việc nhận nội dung ngôn ngữ đích.

## 6. Actor và luồng chính

- Quản trị viên tạo batch: chọn nguồn, đích và chế độ xử lý.
- Worker: nhận diện nguồn, dịch, kiểm tra, tạo phụ đề/TTS và lưu metadata.
- Quản trị viên retry: job giữ nguyên cặp ngôn ngữ ban đầu.

## 7. Hệ thống bị ảnh hưởng

- `backend/domain/models.py`
- `backend/pipeline/asr.py`
- `backend/pipeline/translation.py`
- `backend/infrastructure/vietnamese_translator.py` (đổi tên/migration import)
- `backend/main.py`
- `backend/frontend/index.html`, `backend/frontend/js/app.js`
- tests domain/API/ASR/translation/TTS và retry.

## 8. Rủi ro

- Provider dùng mã ngôn ngữ khác nhau; cần catalog ánh xạ rõ ràng.
- Whisper auto detect có thể sai với câu ngắn; phải lưu detected language và confidence để chẩn đoán.
- Một số Edge voice có thể thay đổi theo runtime; cần fallback gTTS hoặc lỗi rõ ràng.
- Font hiện tại cần đủ glyph CJK/Thái/Hàn; cần test render artifact.
- MyMemory giới hạn 500 byte/request và quota; batching/fallback phải giữ backoff hiện tại.
- OCR nguồn không phải Trung nếu vẫn cho chọn có thể tạo kết quả sai; UI và API phải chặn rõ.

## 9. Câu hỏi và giả định

- `ASSUMPTION A01`: Giai đoạn đầu hỗ trợ 8 ngôn ngữ trong bảng trên.
- `ASSUMPTION A02`: “Đa ngôn ngữ” áp dụng cả nguồn và đích đối với nhận diện bằng giọng nói.
- `ASSUMPTION A03`: OCR đa ngôn ngữ được tách sang giai đoạn sau; OCR hiện tại chỉ dùng khi nguồn là `zh-CN`.
- `ASSUMPTION A04`: Job cũ mặc định `zh-CN → vi` để không phá retry.
- `OPEN Q01`: Có cần bổ sung tiếng Khmer, Lào hoặc Nga ngay giai đoạn đầu không?
- `OPEN Q02`: Có cho phép `source=auto` với chế độ OCR hay buộc chọn `zh-CN`?

## 10. Tiêu chí nghiệm thu cấp cao

- Tạo và retry job với từng ngôn ngữ đích được hỗ trợ mà không mất cấu hình.
- Request nguồn/đích không hỗ trợ hoặc trùng nhau bị từ chối 400 với thông báo rõ.
- Source auto detect được lưu vào artifact và job response.
- Phụ đề chứa script hợp lệ của ngôn ngữ đích không bị đánh dấu thất bại sai.
- TTS dùng đúng locale/voice hoặc fail-fast trước khi tốn thời gian xử lý.
- Job cũ không có trường mới vẫn cho retry theo `zh-CN → vi`.

