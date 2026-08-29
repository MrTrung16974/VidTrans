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
   - `Tự động`: ưu tiên OCR dòng chữ đã có trên video, dùng Whisper làm dự phòng
   - `OCR`: bắt buộc đọc phụ đề Trung đóng cứng trên hình
   - `Whisper`: nhận diện hoàn toàn từ giọng nói
4. Nếu dùng OCR, chỉnh vùng đọc theo tỉ lệ chiều cao video khi phụ đề không nằm gần đáy
5. Bấm **Bắt đầu** → theo dõi tiến trình
6. Tải về file kết quả

Lần OCR đầu tiên sẽ tải model nhận diện chữ Trung và có thể mất nhiều thời gian hơn các lần sau.
Docker Compose lưu model này trong volume `paddle-models`.
Mỗi job còn xuất file `*.translation.json` chứa chữ Trung, bản dịch Việt, confidence OCR,
kết quả đối chiếu ASR và cờ `needs_review` để kiểm tra các cue chưa chắc chắn.

## Thư mục tự tạo

- `uploads/` — file tạm trong quá trình xử lý (tự xóa sau khi xong)
- `outputs/` — video đã dịch, lưu tại đây
