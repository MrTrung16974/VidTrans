# URD — AI social caption generation

- Feature: `ai-social-caption-generation`
- Version: `0.1`
- Trạng thái: `Reviewed for implementation`
- Ngày: 2026-11-06

## Mục tiêu và phạm vi

Tạo ba caption TikTok có văn phong khác nhau từ bản dịch tin cậy, ưu tiên Gemini rồi Qwen3 và fallback local. Không tự publish, không ghi đè caption người dùng, không tuyên bố hashtag trend realtime.

## UC_01 — Gợi ý caption bằng AI

| BR | Quy tắc |
|---|---|
| BR01_01 | Chỉ cue không `needs_review`/`source_fallback` được đưa vào AI. |
| BR01_02 | AI trả đúng 3 variants có hook/body/CTA/hashtag/tone/reason/source cue. |
| BR01_03 | Provider theo thứ tự Gemini → Qwen3 → local v4. |
| BR01_04 | Hashtag AI chỉ chọn từ candidate grounded; tối đa 5. |
| BR01_05 | Số liệu trong output phải xuất hiện trong nguồn; cue index phải hợp lệ. |
| BR01_06 | Chỉ bấm áp dụng mới đổi textarea; publish vẫn cần review. |
| BR01_07 | API giữ `caption`/`generator` để tương thích client cũ. |

| AC | Kết quả mong đợi |
|---|---|
| AC01_01 | Model hợp lệ trả 3 caption khác nhau, mỗi caption ≤350 ký tự. |
| AC01_02 | Timeout/429/JSON sai chuyển provider tiếp theo rồi local. |
| AC01_03 | Hashtag ngoài candidate và claim số không có nguồn bị loại/reject. |
| AC01_04 | UI chọn tone/variant, hiển thị provider thật và không tự apply. |
| AC01_05 | Job/translation vẫn được kiểm tra ownership theo request hiện tại. |

## NFR

- Không log raw prompt/transcript/model output hoặc secret.
- Không DB migration; generation là preview không persistence.
- Backward compatible API và local fallback luôn dùng được.

