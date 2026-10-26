# AI dịch local: Qwen3-4B và TranslateGemma

VidTrans có thể dịch bằng AI chạy ngay trên máy chủ thông qua [Ollama](https://ollama.com): miễn phí, không giới hạn lượt, không gửi nội dung video ra ngoài.

| Model | Dung lượng | Đặc điểm | Vai trò trong VidTrans |
| --- | --- | --- | --- |
| **Qwen3-4B** (`qwen3:4b`) | ~2.5 GB | Đọc cả nhóm câu nên xưng hô (em/anh…) nhất quán; dịch Trung–Việt tốt; cân bằng nhất | Bộ dịch chính tuỳ chọn; **bộ dịch phụ ưu tiên** |
| **TranslateGemma** (`translategemma`, bản 4B) | ~3.3 GB | Model chuyên dịch của Google (55 ngôn ngữ); dịch từng câu riêng lẻ | Bộ dịch chính tuỳ chọn; bộ dịch phụ khi không có Qwen3 |

Chỉ cần cài một trong hai. Cài cả hai thì Qwen3 được ưu tiên làm bộ dịch phụ.

## 1. Yêu cầu phần cứng

| Cấu hình | Qwen3-4B / TranslateGemma 4B |
| --- | --- |
| RAM trống | Tối thiểu 6 GB, khuyên dùng 8 GB trở lên |
| Ổ đĩa | ~3 GB cho mỗi model |
| Tăng tốc | Mac Apple Silicon (M1–M4) dùng GPU tích hợp tự động; NVIDIA cần driver CUDA; chỉ CPU vẫn chạy được nhưng chậm |

Ước lượng tốc độ: Mac M-series dịch một video 1 phút trong khoảng 10–30 giây; máy chỉ có CPU có thể mất vài phút.

## 2. Cài Ollama

**macOS**

```bash
brew install ollama
brew services start ollama      # chạy nền và tự bật cùng máy
```

Hoặc tải ứng dụng tại https://ollama.com/download (ứng dụng tự chạy nền).

**Linux (VPS / máy chủ)**

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
```

**Windows**: tải bộ cài tại https://ollama.com/download.

Kiểm tra Ollama đã chạy:

```bash
curl http://localhost:11434/api/version
```

## 3. Tải model

```bash
ollama pull qwen3:4b           # Qwen3-4B, ~2.5 GB
ollama pull translategemma     # TranslateGemma 4B, ~3.3 GB
ollama list                    # xác nhận tên model đã có
```

Các bản khác (đổi tên model qua biến môi trường ở mục 5):

| Tên model | Dung lượng | Khi nào dùng |
| --- | --- | --- |
| `qwen3:4b-instruct` | 2.5 GB | Bản không có chế độ "suy nghĩ"; VidTrans tự xử lý được cả hai loại |
| `qwen3:4b-instruct-2507-q8_0` | 4.3 GB | Chất lượng cao hơn một chút, cần thêm RAM |
| `translategemma:12b` | 8.1 GB | Dịch tốt hơn rõ rệt, cần ≥ 16 GB RAM |
| `translategemma:27b` | 17 GB | Chất lượng cao nhất, cần máy mạnh (≥ 32 GB RAM hoặc GPU lớn) |

## 4. Thử model trước khi dùng

**Qwen3-4B:**

```bash
curl -s http://localhost:11434/api/chat -d '{
  "model": "qwen3:4b",
  "think": false,
  "stream": false,
  "messages": [{"role": "user", "content": "Dịch sang tiếng Việt, chỉ trả lời bản dịch: 本來是挺愛你的"}]
}' | python3 -c "import sys, json; print(json.load(sys.stdin)['message']['content'])"
```

**TranslateGemma** (dùng đúng mẫu prompt chính thức mà VidTrans gửi):

```bash
curl -s http://localhost:11434/api/chat -d '{
  "model": "translategemma",
  "stream": false,
  "messages": [{"role": "user", "content": "You are a professional Chinese (zh-CN) to Vietnamese (vi) translator. Your goal is to accurately convey the meaning and nuances of the original Chinese text while adhering to Vietnamese grammar, vocabulary, and cultural sensitivities.\nProduce only the Vietnamese translation, without any additional explanations or commentary. Please translate the following Chinese text into Vietnamese:\n\n\n本來是挺愛你的"}]
}' | python3 -c "import sys, json; print(json.load(sys.stdin)['message']['content'])"
```

Kết quả mong đợi là một câu tiếng Việt, ví dụ "Vốn dĩ em rất yêu anh". Lần gọi đầu chậm hơn vì Ollama phải nạp model vào bộ nhớ.

## 5. Kết nối VidTrans với Ollama

| Biến môi trường | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `VIDTRANS_OLLAMA_URL` | `http://localhost:11434` (Docker: `http://host.docker.internal:11434`) | Địa chỉ Ollama |
| `VIDTRANS_QWEN3_MODEL` | `qwen3:4b` | Tên model Qwen3; phải trùng **chính xác** tên trong `ollama list` |
| `VIDTRANS_TRANSLATEGEMMA_MODEL` | `translategemma` | Tên model TranslateGemma |
| `VIDTRANS_QWEN3_ENABLED` / `VIDTRANS_TRANSLATEGEMMA_ENABLED` | `1` | Đặt `0` để tắt hẳn một model |
| `VIDTRANS_OLLAMA_KEEP_ALIVE` | `30s` | Nhả model khỏi RAM sau 30 giây không còn request dịch |
| `VIDTRANS_TRANSLATEGEMMA_NUM_CTX` | `2048` | Context gọn cho mô hình dịch từng câu, giảm KV memory |
| `VIDTRANS_QWEN3_NUM_CTX` | `8192` | Context an toàn cho batch dài và lịch sử hội thoại của video 2–3 giờ |

### Chạy VidTrans trực tiếp (uvicorn)

Không cần cấu hình thêm nếu Ollama chạy trên cùng máy với cổng mặc định.

### Chạy VidTrans bằng Docker

`docker-compose.yml` đã trỏ sẵn tới `http://host.docker.internal:11434` và khai báo `extra_hosts` cho Linux.

- **macOS / Windows (Docker Desktop)**: không cần làm gì thêm.
- **Linux**: Ollama mặc định chỉ nghe `127.0.0.1`, container không gọi tới được. Cho Ollama nghe trên mọi địa chỉ:

  ```bash
  sudo systemctl edit ollama
  # thêm vào phần [Service]:
  #   Environment="OLLAMA_HOST=0.0.0.0:11434"
  sudo systemctl restart ollama
  ```

  > **Bảo mật:** Ollama không có mật khẩu. Trên VPS, chặn cổng 11434 từ Internet, ví dụ `sudo ufw deny 11434`, và chỉ cho mạng Docker truy cập.

Kiểm tra từ bên trong container:

```bash
docker exec vidtrans python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags').read()[:200])"
```

Sau đó khởi động lại VidTrans (`./docker-rebuild.sh` hoặc `docker compose up -d`).

## 6. Dùng trong VidTrans

Mở form tạo job, mục **Bộ dịch**:

- **Qwen3-4B — AI local cân bằng nhất** hoặc **TranslateGemma — AI dịch local**: dùng làm bộ dịch chính.
- Nếu lựa chọn bị mờ kèm chữ "chưa chạy Ollama", tức là VidTrans không thấy model. Kiểm tra lại mục 2–5. VidTrans tự kiểm tra lại sau tối đa 30 giây, không cần khởi động lại.

**Bộ dịch phụ tự động:** khi chọn Google hoặc Gemini mà Ollama đang có model, câu nào dịch lỗi sẽ được AI local dịch thay, thay vì giữ nguyên tiếng gốc. Nếu Gemini hết hạn mức (HTTP 429), phần còn lại của video chuyển hẳn sang AI local. Trong file `*.translation.json`, trường `translation_provider` có dạng `google+qwen3-qwen3:4b` khi bộ dịch phụ đã được dùng.

## 7. Tối ưu tốc độ (tuỳ chọn)

Đặt cho dịch vụ Ollama (mục `[Service]` trên Linux, hoặc `launchctl setenv` trên macOS):

| Biến | Gợi ý | Tác dụng |
| --- | --- | --- |
| `OLLAMA_KEEP_ALIVE` | `5m` hoặc thấp hơn | Mức mặc định của Ollama; VidTrans ghi đè mỗi request bằng `VIDTRANS_OLLAMA_KEEP_ALIVE=30s` |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | Máy ít RAM: chỉ giữ một model trong bộ nhớ |
| `OLLAMA_FLASH_ATTENTION` | `1` | Giảm bộ nhớ, tăng tốc trên GPU hỗ trợ |

## 8. Xử lý sự cố

| Hiện tượng | Nguyên nhân / cách xử lý |
| --- | --- |
| Lựa chọn bị mờ "chưa chạy Ollama" | Ollama chưa chạy (`curl localhost:11434/api/version`), hoặc tên model khác biến cấu hình. Ví dụ đã tải `qwen3:8b` nhưng biến vẫn là `qwen3:4b` |
| Job báo "Không kết nối được Ollama" | Docker trên Linux: xem mục 5 (`OLLAMA_HOST=0.0.0.0`) |
| Job báo "Ollama chưa có model …" | Chạy lệnh `ollama pull <tên model>` được ghi trong thông báo |
| Dịch rất chậm / RAM không nhả sau khi dịch | Máy đang chạy bằng CPU hoặc thiếu RAM khiến hệ thống dùng swap. Dùng bản 4B, đóng ứng dụng khác; có thể hạ `VIDTRANS_OLLAMA_KEEP_ALIVE=10s` |
| Một vài câu Qwen3 dịch lại từng câu | Model nhỏ đôi khi trả sai số câu khi nhóm câu dài; VidTrans tự dịch lại từng câu nên không mất câu nào, chỉ chậm hơn |
| Xưng hô không nhất quán khi dùng TranslateGemma | TranslateGemma không nhìn câu xung quanh; nên dùng Qwen3 hoặc Gemini cho video hội thoại |

Xem log Ollama: `journalctl -u ollama -f` (Linux) hoặc `~/.ollama/logs/server.log` (macOS).
