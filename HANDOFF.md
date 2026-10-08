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
