# Nhà cung cấp AI lồng tiếng

VidTrans giữ bốn lựa chọn để giao diện gọn và có phương án miễn phí thực tế:

| Nhà cung cấp | Khi nên dùng | Cấu hình |
| --- | --- | --- |
| Edge TTS | Mặc định, không cần tài khoản hay API key | Không cần cấu hình |
| Azure AI Speech | API chính thức, hạn mức F0 lớn, giọng Việt Hoài My/Nam Minh | `VIDTRANS_AZURE_SPEECH_KEY`, `VIDTRANS_AZURE_SPEECH_REGION` |
| ElevenLabs | Ưu tiên độ tự nhiên; gói Free có credit hằng tháng | `VIDTRANS_ELEVENLABS_API_KEY` và danh sách giọng `VIDTRANS_ELEVENLABS_VOICES` |
| CIT Voice Studio | Giọng Việt VieNeu-TTS chạy local, miễn phí, không giới hạn ký tự; cần mở app trên máy chủ | `VIDTRANS_CIT_VOICE_URL` (và `VIDTRANS_CIT_VOICE_API_KEY` khi bật truy cập LAN) |

## Lấy khóa hợp lệ

Không dùng khóa chia sẻ trên mạng. Khóa phải được tạo trong chính tài khoản của chủ hệ thống và chỉ lưu ở `.env` trên máy chủ.

### Azure AI Speech

1. Tạo tài khoản Azure và một Speech resource ở gói F0 nếu khu vực còn hỗ trợ.
2. Mở trang **Keys and Endpoint** của resource để lấy Key và Region.
3. Điền hai biến `VIDTRANS_AZURE_SPEECH_KEY` và `VIDTRANS_AZURE_SPEECH_REGION`.

Tài liệu: https://learn.microsoft.com/azure/ai-services/speech-service/rest-text-to-speech

### ElevenLabs

1. Tạo tài khoản Free và tạo API key trong phần tài khoản.
2. Chọn các giọng muốn dùng trong Voice Library, bấm **Add to My Voices** cho từng giọng (tránh lỗi 409 `already_running`) và sao chép voice ID.
3. Điền `VIDTRANS_ELEVENLABS_API_KEY` và `VIDTRANS_ELEVENLABS_VOICES`, mỗi giọng dạng `Tên|female|voice_id` hoặc `Tên|male|voice_id`, ngăn cách bằng `;`:

   ```env
   VIDTRANS_ELEVENLABS_VOICES=Nữ kể chuyện|female|xxxx;Nữ trẻ|female|yyyy;Nam trầm|male|zzzz
   ```

   Khi chọn ElevenLabs, ô **Giọng đọc** hiện toàn bộ giọng đã khai báo trong danh sách, kèm nhãn **Nam/Nữ**. Giọng được chọn sẽ dùng cho toàn bộ câu; dòng sai định dạng bị bỏ qua và ghi cảnh báo vào log. API không trả voice ID ra trình duyệt.

   Nút **▶ Nghe thử** cạnh ô **Giọng đọc** phát bản preview có sẵn của ElevenLabs (không tốn credit). Máy chủ lấy `preview_url` của giọng qua API, tải về một lần rồi giữ trong bộ nhớ; bản preview do ElevenLabs/tác giả giọng thu nên không phản ánh chất giọng đã chọn trên form.

   Hai biến cũ `VIDTRANS_ELEVENLABS_FEMALE_VOICE_ID` / `VIDTRANS_ELEVENLABS_MALE_VOICE_ID` vẫn được hỗ trợ và gộp vào danh sách.

Trang API: https://elevenlabs.io/api và bảng giá: https://elevenlabs.io/pricing

### CIT Voice Studio

[CIT Voice Studio](https://github.com/Cuongyd196/cit-voice-studio) là ứng dụng desktop (Windows, Linux, macOS) đóng gói
VieNeu-TTS; repo chỉ phát hành bộ cài, không có mã nguồn. Khi app đang mở, nó chạy API tại `http://127.0.0.1:8001`
và VidTrans gọi `GET /health`, `GET /voices`, `POST /api/tts/generate` của API đó.

1. Tải bộ cài ở trang Releases của repo, cài và mở app **trên chính máy chạy VidTrans**, đồng ý điều khoản.
2. Đặt URL:
   - chạy backend trực tiếp: `VIDTRANS_CIT_VOICE_URL=http://127.0.0.1:8001`
   - chạy trong Docker: `VIDTRANS_CIT_VOICE_URL=http://host.docker.internal:8001`
3. Nếu container báo không kết nối được (app chỉ nhận kết nối từ cùng máy): trong app bật **Cài đặt → Cho máy khác truy cập
   API**, bấm **Khởi động lại ngay**, rồi chép khoá API vào `VIDTRANS_CIT_VOICE_API_KEY`.
4. Tùy chọn: `VIDTRANS_CIT_VOICE_FEMALE` / `VIDTRANS_CIT_VOICE_MALE` là tên giọng mặc định (ví dụ `Minh Đức`).

Danh sách giọng (gồm cả giọng đã nhân bản trong app) được đọc trực tiếp từ app và kiểm tra lại mỗi 15 giây, nên mở app sau
khi VidTrans đã chạy cũng được. Mô hình chạy bằng CPU, vì vậy VidTrans chỉ gửi `VIDTRANS_CIT_VOICE_CONCURRENCY` (mặc định 1)
request cùng lúc. App không hỗ trợ chỉnh cao độ, nên chế độ tự động chỉ chọn giọng nam/nữ. Khi lồng tiếng mà app bị tắt
giữa chừng hoặc từ chối khoá, job tự chuyển sang Edge TTS như với các nhà cung cấp trả phí.

Ở **Truyện → Video**, chọn **AI lồng tiếng = CIT Voice Studio** rồi chọn giọng nữ và giọng nam: người dẫn chuyện dùng
giọng theo ô **Giọng người dẫn chuyện**, nhân vật dùng giọng theo giới tính của nhân vật. Thẻ `[cười]`, `[thở dài]`,
`[nghỉ 2s]` và từ điển phát âm của app có hiệu lực trong lời thoại. Khi đăng nội dung công khai, ghi rõ "Giọng đọc được
tạo bằng AI" theo điều khoản của app.

Sau khi sửa `.env`, khởi động lại backend. Giao diện gọi `/api/v1/tts-providers` để chỉ bật những nhà cung cấp đã cấu hình đủ; endpoint này không trả khóa bí mật.
