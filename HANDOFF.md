# Handoff — Multilingual translation

- Trạng thái: `IMPLEMENTED_LOCAL`
- Ngày: 2026-10-08
- URD: `BA_URD/multilingual-translation/URD.md`
- Design: `DEV/multilingual-translation/DESIGN.md`
- Test evidence: `TEST/multilingual-translation/TEST_REPORT.md`
- Deploy guide: `deploy/multilingual-translation/RUNBOOK.md`

## Đã hoàn tất

- Nguồn `auto`, Việt, Anh, Trung, Nhật, Hàn, Thái, Indonesia, Tây Ban Nha.
- Đích là một trong 8 ngôn ngữ cụ thể; chặn cặp nguồn/đích trùng.
- Whisper explicit/auto detect; Google/MyMemory động theo cặp ngôn ngữ.
- Validation bản dịch theo script đích; không loại chữ Hán hợp lệ của Trung/Nhật.
- Edge TTS/gTTS và font chọn theo ngôn ngữ đích.
- API, job persistence, resume/retry, artifact và UI giữ source requested/detected/target.
- Job cũ thiếu field tiếp tục mặc định `zh-CN → vi`.
- OCR chữ cháy chỉ hỗ trợ nguồn Trung; UI/API chặn cấu hình không hợp lệ.

## Việc vận hành tiếp theo

Triển khai VPS theo runbook bằng full build, sau đó smoke test một job cũ Trung→Việt, một job `auto→vi`, một job `en→ja` và một job lồng tiếng target Thái. Không cần migration DB hay Camunda.

## Bổ sung 2026-10-18 — caption cháy không có lời thoại

- Nguồn Trung mặc định dùng `auto` (OCR + Whisper), thay cho Whisper-only.
- Pipeline hợp nhất OCR/ASR theo timeline để giữ caption hiện trên hình trong đoạn soundtrack im tiếng và vẫn giữ lời thoại không có caption.
- Checkpoint transcript đã bump fingerprint; source method mới là `hybrid`.
- Default mix mới: voice `1.30`, original audio `0.30`.
- Nhóm test ảnh hưởng: 35/35 PASS. Full suite còn 2 lỗi tồn tại ngoài phạm vi thay đổi; xem `TEST/media-pipeline-optimization/TEST_REPORT.md`.
- Cần full build/restart service và chạy lại video nguồn; không thể phục hồi sạch từ output đã mix/burn.
