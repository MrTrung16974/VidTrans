#!/bin/bash
set -e

echo "=== [1/4] Cài ffmpeg ==="
if ! command -v ffmpeg &>/dev/null; then
  brew install ffmpeg
else
  echo "ffmpeg đã có, bỏ qua."
fi

echo ""
echo "=== [2/4] Cài libass ==="
brew install libass 2>/dev/null || true

echo ""
echo "=== [3/4] Tạo venv và cài packages ==="
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install Pillow numpy
pip install -r requirements.txt

echo ""
echo "=== [4/4] Chạy server ==="
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload