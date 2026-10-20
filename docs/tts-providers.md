# Nhà cung cấp AI lồng tiếng

VidTrans chỉ giữ ba lựa chọn để giao diện gọn và có phương án miễn phí thực tế:

| Nhà cung cấp | Khi nên dùng | Cấu hình |
| --- | --- | --- |
| Edge TTS | Mặc định, không cần tài khoản hay API key | Không cần cấu hình |
| Azure AI Speech | API chính thức, hạn mức F0 lớn, giọng Việt Hoài My/Nam Minh | `VIDTRANS_AZURE_SPEECH_KEY`, `VIDTRANS_AZURE_SPEECH_REGION` |
| ElevenLabs | Ưu tiên độ tự nhiên; gói Free có credit hằng tháng | API key, voice ID nữ và nam trong các biến `VIDTRANS_ELEVENLABS_*` |

## Lấy khóa hợp lệ

Không dùng khóa chia sẻ trên mạng. Khóa phải được tạo trong chính tài khoản của chủ hệ thống và chỉ lưu ở `.env` trên máy chủ.

### Azure AI Speech

1. Tạo tài khoản Azure và một Speech resource ở gói F0 nếu khu vực còn hỗ trợ.
2. Mở trang **Keys and Endpoint** của resource để lấy Key và Region.
3. Điền hai biến `VIDTRANS_AZURE_SPEECH_KEY` và `VIDTRANS_AZURE_SPEECH_REGION`.

Tài liệu: https://learn.microsoft.com/azure/ai-services/speech-service/rest-text-to-speech

### ElevenLabs

1. Tạo tài khoản Free và tạo API key trong phần tài khoản.
2. Chọn một giọng nữ và một giọng nam trong Voice Library, sao chép voice ID tương ứng.
3. Điền `VIDTRANS_ELEVENLABS_API_KEY`, `VIDTRANS_ELEVENLABS_FEMALE_VOICE_ID` và `VIDTRANS_ELEVENLABS_MALE_VOICE_ID`.

Trang API: https://elevenlabs.io/api và bảng giá: https://elevenlabs.io/pricing

Sau khi sửa `.env`, khởi động lại backend. Giao diện gọi `/api/v1/tts-providers` để chỉ bật những nhà cung cấp đã cấu hình đủ; endpoint này không trả khóa bí mật.
