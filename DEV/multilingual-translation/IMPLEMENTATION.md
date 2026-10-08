# Implementation — Dịch đa ngôn ngữ

## Thay đổi

- Catalog 8 ngôn ngữ và mappings Whisper/Google/MyMemory/Edge/gTTS/font.
- Domain validate source/target, cặp trùng và giới hạn OCR.
- ASR explicit hoặc auto detect, trả detected language/probability.
- Translator động theo cặp ngôn ngữ; compatibility alias giữ import cũ.
- Pipeline không còn coi mọi chữ Hán ở output là lỗi.
- API/job/resume/artifact giữ source requested, detected và target.
- TTS chọn voice/locale động.
- UI có hai select, copy đa ngôn ngữ, vô hiệu OCR khi nguồn không phải Trung.
- Docker bổ sung `fonts-noto-core` cho Thai/Latin và dùng profile font theo target.

## Kiểm chứng đã chạy

- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 -m unittest backend.tests.test_languages backend.tests.test_asr backend.tests.test_vietnamese_translator backend.tests.test_translation_pipeline`: 31 test PASS.
- `node --check` trên bản sao `.mjs` của `app.js`: PASS.
- `git diff --check`: PASS tại thời điểm kiểm tra.
- Docker code rebuild: PASS; container healthy; `import main`: PASS.
- OpenAPI/HTML local: có `source_language=zh-CN`, `target_language=vi` và hai field UI.

## Giới hạn suite toàn repo

Host suite chạy 181 test nhưng không xanh hoàn toàn vì các vấn đề ngoài feature: module `douyin_qr_auth` không tồn tại, host thiếu `python-multipart`, một auth token test có failure. Các test feature chạy độc lập và trong Docker đều PASS.
