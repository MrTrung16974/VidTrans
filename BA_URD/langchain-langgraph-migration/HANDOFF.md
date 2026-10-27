# Handoff — LangChain/LangGraph migration

- Workspace: `VidTrans`
- Feature: `langchain-langgraph-migration`
- Cập nhật: `2026-10-27`
- Giai đoạn: phân tích và lập plan
- Trạng thái: `IN_REVIEW`

## Mục tiêu và phạm vi

Lập kế hoạch chuyển lớp orchestration của background media/AI/publishing sang LangGraph và
chuẩn hóa LLM boundary bằng LangChain. Phiên này không sửa source code, dependency, DB hay deploy.

## Đọc tiếp theo thứ tự

1. `BA_URD/langchain-langgraph-migration/ANALYSIS.md` (`0.1-draft`)
2. `BA_URD/langchain-langgraph-migration/PLAN.md` (`0.1`, `IN_REVIEW`)
3. `ARCHITECTURE.md`

## Đã làm

- Kiểm kê scheduler/service, video pipeline, comic pipeline, provider LLM/translation,
  artifact checkpoint và social publishing.
- Xác định TO-BE gồm ba graph chính và model gateway LangChain.
- Đề xuất giữ `JobStore` làm lifecycle source of truth và `StageCheckpointStore` quản lý media artifact.
- Lập roadmap pilot comic → video → publishing, canary và rollback.

## Review/chặn

- 2026-10-27 người dùng chốt: phạm vi media/AI/publish; tracing no-op + LangSmith opt-in; cho phép
  schema checkpoint PostgreSQL sau spike + ADR; human review tắt mặc định. PLAN nâng lên `0.2`.
- Đã lập `DEV/langchain-langgraph-migration/DEVELOPMENT_PLAN.md` (S0–S9, ~55–65 ngày công);
  chờ người dùng duyệt trước khi bắt đầu S0.

## Kiểm chứng

- Đã kiểm tra file được tạo và trạng thái Git.
- Không chạy test vì chỉ thay đổi tài liệu.

## Bước kế tiếp sau khi được duyệt

Viết `URD.md`/`OPEN_QUESTIONS.md`, thu baseline và làm dependency/checkpointer spike; chưa coding
production trước khi đóng các blocker.

Prompt tiếp tục: `Dùng $feature-delivery-workflow. Đọc BA_URD/langchain-langgraph-migration/HANDOFF.md và các file được dẫn; kiểm tra trạng thái hiện tại, tiếp tục bước đã được duyệt, không lặp lại phần đã hoàn tất.`
