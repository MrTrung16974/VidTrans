# Nhà cung cấp AI lồng tiếng

VidTrans chỉ giữ ba lựa chọn để giao diện gọn và có phương án miễn phí thực tế:

| Nhà cung cấp | Khi nên dùng | Cấu hình |
| --- | --- | --- |
| Edge TTS | Mặc định, không cần tài khoản hay API key | Không cần cấu hình |
| Azure AI Speech | API chính thức, hạn mức F0 lớn, giọng Việt Hoài My/Nam Minh | `VIDTRANS_AZURE_SPEECH_KEY`, `VIDTRANS_AZURE_SPEECH_REGION` |
| ElevenLabs | Ưu tiên độ tự nhiên; gói Free có credit hằng tháng | `VIDTRANS_ELEVENLABS_API_KEY` và danh sách giọng `VIDTRANS_ELEVENLABS_VOICES` |

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

Sau khi sửa `.env`, khởi động lại backend. Giao diện gọi `/api/v1/tts-providers` để chỉ bật những nhà cung cấp đã cấu hình đủ; endpoint này không trả khóa bí mật.
