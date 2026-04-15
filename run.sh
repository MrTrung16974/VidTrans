# 1. Cài ffmpeg
brew install ffmpeg

# 2. Cài packages
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. Chạy server
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload