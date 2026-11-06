# Thiết kế — AI social caption generation

- Plan: `BA_URD/ai-social-caption-generation/PLAN.md` v0.1, người dùng duyệt ngày 2026-11-06.
- Phạm vi: TikTok text-first; 3 variants; Gemini → Qwen3 → local v4; không trend realtime/multimodal.

## Mapping và kiến trúc

- Route/ownership: `application/tiktok_browser_routes.py::suggest_caption` tiếp tục dùng `ready_job`, nên object authorization và tenant isolation không đổi.
- Application service mới: `application/social_caption_suggestions.py` chịu prompt, schema, parse/validate, provider chain và fallback.
- LLM integration: tái sử dụng `GeminiJSONClient`, `OllamaJSONClient`, `StoryLLM`; không đọc/log API key.
- Local fallback: `pipeline/tiktok.py::LocalExtractiveTikTokProvider` giữ nguyên.
- UI web: `web/index.html`, `web/js/tiktok.js` cho chọn tone và một trong các variants; chỉ bấm “Dùng caption này” mới sửa textarea.
- UI Android: `mobile/.../social_publish_screen.dart` hiển thị tone, variants và hashtag chips; client chỉ gọi endpoint preview đã có object authorization.

## Contract

`POST /api/v1/jobs/{job_id}/tiktok-caption?tone=<auto|natural|curious|emotional|expert>` giữ hai field cũ:

- `caption`: caption variant đầu/fallback.
- `generator`: model thực tế hoặc `local-extractive-v4`.

Thêm:

- `variants[]`: `caption`, `hook`, `body`, `cta`, `hashtags`, `tone`, `reason`, `source_cues`.
- `fallback`: boolean.

## Guardrail

- Chỉ đưa cue đã lọc `needs_review`/`source_fallback` vào prompt.
- Schema yêu cầu cue index; index phải hợp lệ.
- Số xuất hiện trong output phải tồn tại trong nguồn.
- Hashtag AI chỉ được chọn từ candidate đã sinh bằng local v4; tối đa 5, unique.
- Caption chuẩn hóa và giới hạn 350 ký tự; variants trùng bị loại.
- Không log prompt/raw output. LLM lỗi/timeout/429/invalid output chuyển provider kế tiếp rồi local.
- Preview không persistence, không publish, không ghi đè caption đang sửa.

## Cấu hình và entitlement

- Tái sử dụng cấu hình Gemini/Ollama hiện hữu; không thêm biến môi trường.
- Giả định được duyệt: AI dùng khi server có provider; mọi tài khoản vẫn có local fallback. Feature entitlement riêng được để ngoài release này để không làm gián đoạn chức năng hiện hữu.

## Test strategy

- Unit: 3 variants hợp lệ, hashtag candidate-only, number/cue/duplicate rejection, provider failover, local fallback.
- API: backward-compatible fields, ownership hiện hữu, tone validation.
- Frontend static: tone selector/variants/provider rendering và apply-by-selection.
- Regression: `test_tiktok_summary.py`, `test_tiktok_browser.py`.
- Flutter: parse contract cũ/mới và UI chọn variant mà không tự ghi đè caption.

