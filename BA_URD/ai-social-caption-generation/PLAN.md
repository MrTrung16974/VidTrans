# Kế hoạch — Tạo caption và hashtag bằng AI

- Feature: `ai-social-caption-generation`
- Version: `0.1`
- Trạng thái: `IMPLEMENTED_LOCAL_DEPLOY_PENDING`
- Phạm vi đề xuất: AI text-first cho TikTok, 3 variants, validator và local fallback; chưa dùng video multimodal/trend realtime.

## Definition of Done

- AI provider dùng abstraction hiện hữu, trả JSON schema đã validate.
- Local v4 luôn sẵn làm fallback.
- UI hiển thị 3 biến thể, tone và provider thật; không tự áp dụng/publish.
- Có test schema, limit, hallucination guard, timeout/429/fallback, authorization và regression artifact/publish.
- Có feature entitlement/quota rõ ràng trước deploy.

## Các bước

| Bước | Việc làm | Đầu ra | Kiểm chứng | Trạng thái |
|---|---|---|---|---|
| 1. BA | Chốt provider, entitlement, platform, tone và chính sách trend | URD/open questions | User review | DONE |
| 2. Design | Thiết kế prompt/schema/validator/fallback/API/UI | `DEV/.../DESIGN.md` | Design review | DONE |
| 3. Backend | AI provider + guardrail + fallback + API variants | Source/unit tests | Unit/contract tests | DONE |
| 4. Frontend | Tone/variant picker, provider badge, apply/review | Web tests | Static/browser tests | DONE |
| 5. Test | Regression local generator, tenant/auth, failure modes | TEST artifacts | PASS evidence | DONE_LOCAL |
| 6. Deploy | Config contract, rebuild, smoke, rollback | DEPLOY artifacts | VPS smoke | BLOCKED_SSH_TIMEOUT |

## Rủi ro và xử lý

- AI bịa/thêm claim: source grounding + validator + review bắt buộc.
- Chi phí/429: cache theo translation checksum + prompt version; quota; local fallback.
- Caption na ná nhau: yêu cầu mỗi variant khác tone/angle và kiểm tra similarity.
- Hashtag rác: whitelist/normalizer, relevance check, giới hạn 5.
- “Trend realtime” giả: chỉ hiển thị trend khi có nguồn dữ liệu ngoài model.

## Tự review

- Tái sử dụng hạ tầng LLM hiện hữu; không thêm client/secret mới không cần thiết.
- Không thay publishing snapshot/review contract.
- Có fallback và rollback rõ ràng.
- Các câu Q01–Q04 cần chốt trước coding.

## Approval record

| Version/phạm vi | Người duyệt | Thời điểm | Xác nhận |
|---|---|---|---|
| 0.1 |  |  |  |

