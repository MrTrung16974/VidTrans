# Thiết kế — Dịch đa ngôn ngữ

- PLAN: 0.1 đã được người dùng duyệt.
- URD: 1.0.

## Thành phần

1. `domain/languages.py`: `LanguageProfile`, catalog bất biến, normalize/validate và provider/Whisper/TTS mappings.
2. `domain/models.py`: `ProcessingRequest` chứa source/target.
3. `pipeline/asr.py`: method tổng quát `transcribe(..., source_language)` trả `ASRResult(segments, detected_language, probability)`; wrapper cũ giữ compatibility nếu cần.
4. `infrastructure/translator.py`: translator tổng quát. File cũ giữ re-export để không phá import.
5. `pipeline/translation.py`: nhận source/target; validation theo target và metadata động.
6. `main.py`: truyền ngôn ngữ xuyên pipeline, chọn voice/TTS locale từ catalog, legacy defaults.
7. frontend: hai select nguồn/đích; copy và preview thay theo target.

## Contract

- Internal canonical code: `vi`, `en`, `zh-CN`, `ja`, `ko`, `th`, `id`, `es`.
- Whisper: `zh-CN → zh`, còn lại giữ mã; `auto → None`.
- Google/MyMemory nhận mapping riêng từ profile.
- TTS lookup bằng target; voice type vẫn `female/male`.

## Validation và lỗi

- Input/catalog/cặp trùng: `ValueError` → HTTP 400.
- Mode dubbed thiếu voice profile: HTTP 400 trước enqueue.
- Provider/network/rate limit: cơ chế hiện tại, thông báo không chứa query/credential.
- Output invalid: rỗng hoặc giống source; kiểm tra script chỉ áp dụng khi target không dùng script đó và tỷ lệ còn quá cao.

## Compatibility

- Form defaults và resume defaults: `zh-CN/vi`.
- Giữ class/import cũ dưới alias trong một chu kỳ.
- Artifact version tăng và vẫn giữ các field cũ.

## Test strategy

- Catalog/domain boundary và legacy default.
- Mock faster/openai Whisper options explicit/auto.
- Provider query parameters cho nhiều cặp; Google 429 fallback.
- Target Trung/Nhật được chấp nhận; unchanged bị chặn.
- TTS voice/locale mapping.
- API/batch/resume fields và UI FormData.

