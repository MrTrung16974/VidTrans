# Phân tích — Tạo caption và hashtag bằng AI

- Feature: `ai-social-caption-generation`
- Ngày: 2026-11-06
- Trạng thái: `DRAFT`

## Kết luận nhanh

- `CONFIRMED`: Caption/hashtag hiện tại **không dùng AI tạo sinh**. `LocalExtractiveTikTokProvider` v4 chạy local, chọn 1–2 câu từ bản dịch, thêm CTA theo bộ luật và hashtag theo vocabulary/keyphrase.
- `CONFIRMED`: Nút UI ghi “Gợi ý caption bằng AI”, nhưng API `/api/v1/jobs/{job_id}/tiktok-caption` vẫn gọi bộ local deterministic. Nhãn UI hiện mô tả quá khả năng thật.
- `CONFIRMED`: Dự án đã có `GeminiJSONClient`/Qwen3 abstraction, JSON schema, timeout/error handling và heuristic fallback ở các tính năng khác, nên có thể tái sử dụng thay vì viết client AI mới.
- Đề xuất: dùng mô hình **hybrid AI-first, local fallback**. AI viết biến thể sáng tạo có cấu trúc; validator kiểm soát độ dài, hashtag, dữ kiện và nội dung rủi ro; local v4 tiếp tục làm fallback ổn định.

## Luồng hiện tại (AS-IS)

1. Sau khi dịch, pipeline truyền `translated_segments` vào `LocalExtractiveTikTokProvider.generate`.
2. Loại câu trùng và ưu tiên cue không có `needs_review`.
3. Chấm điểm token/tần suất/vị trí để chọn hook và tối đa một câu tiếp nối.
4. Nhận diện chủ đề bằng `_HASHTAG_PHRASES` và `_THEME_RULES` hard-code.
5. Nếu không khớp chủ đề, trích cụm 2–4 token làm hashtag.
6. Thêm CTA theo nhóm chủ đề; thêm tối đa một `#xuhuong`; giới hạn 5 hashtag.
7. Ghi `*.tiktok.json`/`*.tiktok.txt`; UI cho xem và bấm áp dụng. Luồng publish vẫn yêu cầu người dùng duyệt.

### Điểm mạnh

- Nhanh, miễn phí, deterministic, không phụ thuộc mạng/API.
- Không bịa dữ kiện ngoài transcript; test dễ và fallback tốt.
- Có lọc `needs_review`, giới hạn caption/hashtag và tách relevance/discovery tag.

### Hạn chế

- Caption thường giống phụ đề ghép lại, chưa viết hook sáng tạo theo ngữ cảnh tổng thể.
- Vocabulary chủ đề hữu hạn; hashtag động có thể máy móc và không hiểu sắc thái.
- Không tạo nhiều tone/biến thể theo TikTok, Reels, YouTube Shorts hoặc mục tiêu tương tác.
- `#xuhuong` là rule tĩnh, không phải dữ liệu trend realtime.
- Nút “AI” hiện gây hiểu nhầm vì endpoint không gọi model.

## TO-BE đề xuất

### Kiến trúc hybrid

`trusted translation + metadata` → `AI JSON generator` → `validator/normalizer` → `3 variants` → `user review/apply`

Nếu model thiếu cấu hình, timeout, 429, output sai schema hoặc không qua validator → `LocalExtractiveTikTokProvider v4`.

### Input AI giai đoạn 1

- Cue dịch tin cậy, giữ thứ tự; bỏ `needs_review`/`source_fallback`.
- Ngôn ngữ, nền tảng, giới hạn ký tự/hashtag.
- Tone do người dùng chọn: tự nhiên, tò mò, cảm xúc, chuyên gia, bán hàng nhẹ.
- Mục tiêu: tương tác, chia sẻ, chuyển đổi hoặc nhận diện.

Không cần gửi nguyên video trong giai đoạn đầu. Giai đoạn 2 mới cân nhắc Gemini multimodal với 3–4 frame đại diện để hiểu hình ảnh khi transcript ít.

### Output JSON có schema

- `variants[]`: `hook`, `body`, `cta`, `relevance_hashtags`, `discovery_hashtags`, `caption`.
- `reason`: lý do ngắn để người dùng chọn.
- `source_claims[]`: cue index làm căn cứ, giúp hạn chế bịa.
- `generator`: model thực tế hoặc `local-extractive-v4`.

## Guardrail bắt buộc

- Không tự nhận hashtag “đang trend”; trend realtime chỉ được dùng khi có nguồn dữ liệu TikTok hợp lệ và timestamp.
- Hashtag tối đa 5 mặc định; unique, chuẩn hóa, không tag nhạy cảm/không liên quan.
- Caption không thêm tên người, số liệu, giá, khuyến mãi hoặc tuyên bố chưa có trong nguồn/user input.
- Output phải qua schema, length checks và content validation; model lỗi thì fallback local.
- Chỉ preview; không ghi đè caption đang sửa và không tự publish.
- Không log raw caption/transcript/model output đầy đủ; giữ tenant/object authorization hiện tại.

## Hệ thống ảnh hưởng

- `backend/pipeline/tiktok.py`: interface/provider/validator/fallback.
- `backend/infrastructure/llm_providers.py`: tái sử dụng `GeminiJSONClient`/Qwen3.
- `backend/application/tiktok_browser_routes.py`: trả variants/provider/fallback reason.
- `backend/domain/plan_entitlements.py`: quyết định AI caption thuộc gói nào.
- `web/js/tiktok.js`, `web/index.html`: chọn tone/variant và hiển thị provider thật.
- Tests: provider schema, hallucination guard, fallback, tenant isolation, UI apply/review.

## Tiêu chí nghiệm thu sơ bộ

- AC01: AI trả 3 caption khác nhau, đúng schema và giới hạn platform.
- AC02: Mỗi hashtag liên quan trực tiếp input hoặc nguồn trend có timestamp; không tự gắn nhãn realtime.
- AC03: Model timeout/429/invalid JSON tự fallback local, UI vẫn nhận caption dùng được.
- AC04: Cue `needs_review`/`source_fallback` không trở thành claim chắc chắn.
- AC05: Người dùng phải bấm áp dụng và duyệt trước publish; caption đang sửa không bị ghi đè.
- AC06: UI hiển thị chính xác `Gemini/Qwen3/local fallback`, không gọi local là AI.

## Open questions

- `OPEN Q01`: AI caption miễn phí cho mọi user hay chỉ gói trả phí như Gemini translation/comic?
- `OPEN Q02`: ưu tiên Gemini, Qwen3 local hay chế độ `auto`?
- `OPEN Q03`: cần giai đoạn đầu tạo riêng cho TikTok hay đồng thời TikTok/Reels/Shorts?
- `OPEN Q04`: có cần dữ liệu trend realtime không? Nếu có phải chọn nguồn/API hợp lệ, không suy từ model.

