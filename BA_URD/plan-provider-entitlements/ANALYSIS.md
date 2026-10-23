# Phân tích — phân quyền AI theo gói

- Trạng thái: `CONFIRMED` 23/10/2026 (yêu cầu trực tiếp + trả lời 2 câu hỏi làm rõ).
- Quy tắc:

| Loại | Free | Creator / Pro / Enterprise |
|---|---|---|
| Dịch phụ đề | Google Translate, TranslateGemma, Qwen3 | + Gemini AI |
| Lồng tiếng | Edge TTS, CIT Voice Studio, Azure Speech | + ElevenLabs |
| Truyện tranh / Hoạt hình | Kịch bản Qwen3/Heuristic, ảnh ComfyUI/minh họa, 2.5D/GPU worker | + Gemini (kịch bản, ảnh), Google Veo |

- Quyết định: Qwen3 và Azure mở cho mọi gói; Gemini trong comic chỉ gói trả phí.
- Owner/admin không bị giới hạn (vận hành, kiểm thử).
- AC01: user Free tạo job/batch với Gemini hoặc ElevenLabs (khi lồng tiếng) nhận 403; chế độ chỉ phụ đề bỏ qua lựa chọn TTS.
- AC02: chạy lại job / render comic kiểm tra lại theo gói hiện tại của chủ job (hạ gói thì không chạy tiếp AI trả phí).
- AC03: comic "Tự động" với user Free không dùng Gemini (chuyển Qwen3 hoặc chia theo câu).
- AC04: API danh sách provider trả `allowed`/`requires_paid_plan`; UI khóa lựa chọn với nhãn "cần gói Creator/Pro".
