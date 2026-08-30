# VidTrans — Video Translator Web App

## Cấu trúc
```
video_translator/
├── backend/
│   └── main.py          ← FastAPI server
├── frontend/
│   └── index.html       ← Giao diện web
├── requirements.txt
└── README.md
```

## Cài đặt

```bash
pip install -r requirements.txt
```

> Cần có **ffmpeg** trong PATH:
> - Windows: https://ffmpeg.org/download.html
> - Mac: `brew install ffmpeg`
> - Linux: `sudo apt install ffmpeg`

## Chạy server

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Mở trình duyệt: **http://localhost:8000**

## Sử dụng

1. Upload file **video** (mp4, mov, mkv...)
2. Upload file **nhạc nền** (mp3, wav, aac... — ví dụ: `Chill.wav`)
3. Chọn nguồn phụ đề Trung:
   - `Whisper`: nhận diện hoàn toàn từ giọng nói, là lựa chọn mặc định trên Docker Apple Silicon
   - `Tự động`: ưu tiên OCR dòng chữ đã có trên video, dùng Whisper làm dự phòng
   - `OCR`: bắt buộc đọc phụ đề Trung đóng cứng trên hình
4. Nếu dùng OCR, chỉnh vùng đọc theo tỉ lệ chiều cao video khi phụ đề không nằm gần đáy
5. Chọn chế độ giọng đọc:
   - `Tự động theo cao độ giọng gốc`: chạy hoàn toàn local, dùng pitch của từng câu để chọn chất giọng TTS Nam/Nữ.
   - `Chọn thủ công`: dùng một chất giọng Nam hoặc Nữ cho toàn bộ video.
   - Trong chế độ tự động, `Giọng dự phòng` được dùng khi lời thoại quá ngắn, nhiễu hoặc pitch không đủ chắc chắn.
6. Bấm **Bắt đầu** → theo dõi tiến trình
7. Tải về file kết quả

Lần OCR đầu tiên sẽ tải model nhận diện chữ Trung và có thể mất nhiều thời gian hơn các lần sau.
Docker Compose lưu model này trong volume `paddle-models`.
Docker Apple Silicon tắt PaddleOCR mặc định (`VIDTRANS_ENABLE_PADDLE_OCR=0`) vì Paddle có thể làm
process Linux ARM64 bị crash. Chỉ bật lại sau khi đã kiểm tra runtime Paddle tương thích.
Mỗi job còn xuất file `*.translation.json` chứa chữ Trung, bản dịch Việt, confidence OCR,
kết quả đối chiếu ASR, cờ `needs_review` và thông tin chọn giọng cho từng câu.

Trạng thái job được lưu bền vững tại `backend/work/jobs.sqlite3`; restart server không làm
mất lịch sử job. Với cấu hình một worker hiện tại, job đang chạy dở sẽ tự chạy lại từ đầu sau
khi server restart, miễn là file upload còn trong `uploads/`. Xem [ARCHITECTURE.md](ARCHITECTURE.md)
để biết ranh giới module và lộ trình tách hệ thống.

## Thư mục tự tạo

- `uploads/` — file tạm trong quá trình xử lý (tự xóa sau khi xong)
- `outputs/` — video đã dịch, lưu tại đây
