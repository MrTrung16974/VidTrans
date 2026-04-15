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
3. Chỉnh các thông số (âm lượng nhạc, model Whisper, ngôn ngữ gốc)
4. Bấm **Bắt đầu** → theo dõi tiến trình
5. Tải về file kết quả

## Thư mục tự tạo

- `uploads/` — file tạm trong quá trình xử lý (tự xóa sau khi xong)
- `outputs/` — video đã dịch, lưu tại đây
