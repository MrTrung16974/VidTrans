# Kế hoạch phát triển — LangChain/LangGraph migration

- Plan nguồn: `BA_URD/langchain-langgraph-migration/PLAN.md` v0.2 (quyết định chặn đã chốt 2026-10-27)
- Phân tích: `BA_URD/langchain-langgraph-migration/ANALYSIS.md` v0.1-draft
- Ngày: `2026-10-27`
- Trạng thái: `DRAFT` — chờ người dùng duyệt trước khi bắt đầu Sprint 0
- Đơn vị ước lượng: ngày công 1 dev (ideal day), chưa gồm review/QA bên ngoài

## 1. Quyết định đầu vào

| ID | Quyết định | Hệ quả cho dev |
|---|---|---|
| D-01 | Phase đầu: video translation + comic/animation + social publishing | Auth/OAuth, billing, admin, CRUD, retention không đụng tới |
| D-02 | Tracing mặc định no-op; LangSmith chỉ opt-in, metadata allow-list | Không import/gọi LangSmith khi chưa bật; test chứng minh không có network call |
| D-03 | Cho phép schema PostgreSQL riêng cho checkpointer **sau spike + ADR** | Migration SQL chỉ viết ở Sprint 1, sau khi spike Sprint 0 pass |
| D-04 | Human review (`interrupt`) dựng sẵn nhưng tắt mặc định | Không đổi UI/API trong phase đầu; chỉ có test route interrupt |
| D-05 | LangGraph chỉ điều phối; LangChain chỉ ở ranh giới LLM (ADR-01/02) | OCR/ASR/TTS/FFmpeg/checksum giữ nguyên là service Python typed |

## 2. Hiện trạng code đã đối chiếu

| Thành phần | Vị trí | Ghi chú cho migration |
|---|---|---|
| Video orchestration | `backend/main.py::process_video` (dòng 2754, ~745 dòng), `resume_process_video` (dòng 3717) | `main.py` 6.654 dòng; phải tách stage trước khi graph hóa |
| Queue/dispatch | `backend/application/job_scheduler.py` (dispatch theo `job_action`) | Điểm cắm duy nhất để chọn `legacy`/`graph` theo job |
| Comic runner | `backend/application/comic_service.py::ComicJobRunner.run` | Boundary sạch, phù hợp pilot |
| LLM | `backend/infrastructure/llm_providers.py` — protocol `StoryLLM.generate_json`, `GeminiJSONClient`, `OllamaJSONClient`, `create_story_llm` | Gateway LangChain implement đúng `StoryLLM` → thay thế không đổi nơi gọi |
| Storyboard | `backend/pipeline/comic_storyboard.py` | Tái sử dụng validate/repair/heuristic làm node |
| Artifact checkpoint | `backend/infrastructure/stage_checkpoint.py` | Giữ nguyên là nguồn sự thật artifact |
| Publishing | `backend/application/social_publishing.py`, `backend/infrastructure/publishers/` | Graph hóa sau cùng |
| Runtime | `python:3.11-slim`, `psycopg[binary]>=3.2`, `fastapi` chưa pin | psycopg3 khớp `langgraph-checkpoint-postgres`; cần pin version khi thêm dependency |

## 3. Lộ trình sprint

Tổng ước lượng: **~55–65 ngày công**. Mỗi sprint kết thúc bằng một gate; không sang sprint sau khi gate fail.

| Sprint | Nội dung | Ước lượng | Gate |
|---|---|---:|---|
| S0 | Baseline + dependency/checkpointer spike + URD | 4–5 | G0 |
| S1 | Nền tảng `backend/workflows/` + migration checkpoint | 5–6 | — |
| S2 | LangChain model gateway | 4 | — |
| S3 | Pilot storyboard subgraph (shadow) | 4 | G1 |
| S4 | Comic/animation graph + canary comic | 6–7 | G2 |
| S5 | Tách stage `process_video` (refactor thuần, không đổi behavior) | 7–8 | — |
| S6 | Video translation graph + recovery matrix | 8–10 | G3 |
| S7 | Social publishing graph | 6 | G4 |
| S8 | Observability, canary 5→25→50→100%, rollback drill | 5–6 | G5 |
| S9 | Cleanup legacy (sau cửa sổ ổn định) | 3–4 | G6 |

S5 có thể chạy song song với S3–S4 nếu có dev thứ hai, vì không phụ thuộc graph.

### S0 — Baseline và spike (4–5 ngày)

Việc làm:
1. Viết `BA_URD/langchain-langgraph-migration/URD.md` và `OPEN_QUESTIONS.md` từ D-01…D-05.
2. Script baseline (read-only) trên `JobStore`: completion/failure/cancel rate, thời gian theo stage, retry count
   trong 30 ngày gần nhất → `TEST/langchain-langgraph-migration/BASELINE.md`.
3. Spike dependency trong branch riêng: `langgraph`, `langchain-core`, `langchain-google-genai`, `langchain-ollama`,
   `langgraph-checkpoint-postgres`; tạo `requirements-workflow.txt` với version pin chính xác.
4. Kiểm tra tương thích Pydantic v2/FastAPI hiện tại, build image `amd64` + `arm64`, đo chênh lệch image size.
5. Spike checkpointer: `PostgresSaver` dùng psycopg3, schema `vidtrans_graph`, `thread_id = job_id`,
   crash giữa node rồi resume; xác nhận state serialize không chứa binary.
6. Golden dataset storyboard đã ẩn danh (≥ 20 truyện) + fixture translation.

DoD/G0: build 2 kiến trúc pass; image tăng ≤ ngưỡng được duyệt (đề xuất ≤ 150 MB); full test suite hiện tại vẫn pass;
ADR checkpointer viết xong; người dùng duyệt URD.

### S1 — Nền tảng workflow (5–6 ngày)

```text
backend/workflows/
  __init__.py
  contracts.py      # WorkflowEnvelope, error taxonomy, typed partial update
  errors.py         # map domain exception -> TRANSIENT|RATE_LIMIT|INVALID_INPUT|REVIEW_REQUIRED|CANCELLED|PERMANENT|NEEDS_RECONCILIATION
  context.py        # WorkflowContext: job_id, owner_user_id, tenant, cancel check, semaphores (trusted, từ server)
  registry.py       # (graph_name, graph_version) -> compiled graph
  runner.py         # adapter JobScheduler -> graph.invoke/resume
  progress.py       # node event -> JobService progress + job_events + n8n workflow_events
  checkpointing.py  # PostgresSaver factory, schema vidtrans_graph
  tracing.py        # no-op mặc định; LangSmith chỉ khi VIDTRANS_WORKFLOW_TRACING=langsmith
deploy/langchain-langgraph-migration/sql/001_graph_checkpoint.sql
```

- Cấu hình mới: `VIDTRANS_WORKFLOW_ENGINE=legacy|graph_shadow|graph` (mặc định `legacy`),
  `VIDTRANS_WORKFLOW_TRACING=off|langsmith` (mặc định `off`).
- Pin `engine` + `graph_version` vào request của job lúc queue; `job_scheduler.py` đọc giá trị pin, **không** đọc
  config hiện tại, vì vậy job không bao giờ đổi engine giữa chừng.
- Cancellation: node gọi `context.ensure_active()` trước và sau tác vụ dài (bọc `ensure_job_active` hiện hữu).

Test (`backend/tests/test_workflows_foundation.py`): graph mẫu queue → cancel → crash/resume → progress event;
state chỉ chứa kiểu JSON nhỏ; owner lấy từ context, không lấy từ input; `tracing=off` không có network call.

### S2 — LangChain model gateway (4 ngày)

```text
backend/infrastructure/ai/
  models.py      # factory theo provider/model, timeout/retry ở một chỗ
  structured.py  # Pydantic structured output + JSON fallback parse
  errors.py      # map lỗi LangChain/provider -> LLMError/LLMRateLimitedError/LLMUnavailableError
  gateway.py     # LangChainStoryLLM implement StoryLLM.generate_json
```

- `create_story_llm` chọn implementation theo `VIDTRANS_LLM_GATEWAY=legacy|langchain` (mặc định `legacy`) → kill switch.
- Không log prompt/output; callback chỉ ghi provider, model, latency, token count.
- Contract test chạy cùng fixture với test `GeminiJSONClient`/`OllamaJSONClient` hiện có: cùng input → cùng
  exception class và cùng UI-visible message.

### S3 — Pilot storyboard subgraph (4 ngày)

- `backend/workflows/comic/storyboard.py`: `chunk → generate → validate → repair (≤ N lần) → heuristic_fallback`,
  tái sử dụng hàm validate/repair/heuristic trong `pipeline/comic_storyboard.py`.
- `graph_shadow`: chạy song song trên golden dataset (offline), so schema-valid rate, scene coverage, số lần repair.
- G1: schema-valid và coverage ≥ baseline legacy; không tăng thời gian p95 quá 10%.

### S4 — Comic/animation graph (6–7 ngày)

- `backend/workflows/comic/graph.py`: `load_story → storyboard_subgraph → save → [review interrupt, tắt] →
  character_references → scene/shot fan-out (Send API, semaphore hiện hữu) → keyframe → voice → animation → QC →
  retry/2.5D fallback → concat/export → finalize`.
- Artifact key/cache của `ComicProject` giữ nguyên; node đọc cache trước khi tính.
- G2: canary comic (tenant nội bộ) không regression cache/resume/output; kill tại mỗi node rồi resume không render
  lại asset hợp lệ; sửa một shot chỉ invalid đúng downstream.

### S5 — Tách stage `process_video` (7–8 ngày, refactor thuần)

- Chuyển từng khối của `process_video` sang `backend/pipeline/video_stages/` (preflight, source, ocr, asr, reconcile,
  segment_filter, translate, subtitle_layout, voice_route, tts, audio_mix, render, social_content, finalize).
- Mỗi stage: input/output typed, dùng `StageCheckpointStore` như hiện tại. `process_video` gọi tuần tự các stage → behavior không đổi.
- Regression bắt buộc: 3 mode, OCR/ASR hybrid, ≥ 3 ngôn ngữ đích, resume tại mỗi checkpoint; so checksum/semantic output với bản trước refactor.
- Làm theo nhiều PR nhỏ (1–2 stage/PR) để review được.

### S6 — Video translation graph (8–10 ngày)

- `backend/workflows/video/graph.py` ghép các stage của S5: OCR ‖ ASR fan-out/fan-in, mode router (1 | 2/3),
  nhánh social content/publish tách khỏi đường render chính.
- Progress label/event giữ nguyên chuỗi hiện tại; field mới chỉ additive.
- Recovery matrix: kill trước/sau từng node × 3 mode; artifact thiếu/hỏng; `graph_version` cũ.
- G3: recovery matrix pass; overhead orchestration < 2% tổng thời gian job; parallelism OCR/ASR không giảm.

### S7 — Social publishing graph (6 ngày)

- `backend/workflows/publishing/graph.py`: `authorize/resolve_connection → validate_media → create_upload →
  poll → reconcile → publish → persist_receipt`.
- Write-ahead: ghi idempotency key + state trước mọi lời gọi remote; `NEEDS_RECONCILIATION` là nhánh chính thức, không retry mù.
- G4: fault injection timeout/409/429/5xx/mất kết nối giữa chừng → 0 bài đăng trùng; restart → reconcile đúng.

### S8 — Observability và canary (5–6 ngày)

- Metric theo graph/node/provider (latency, error class, retry); trace correlation `job_id → run → node → artifact checksum`.
- Test redaction: trace/checkpoint không chứa transcript, prompt, token, secret.
- Runbook + manifest: `deploy/langchain-langgraph-migration/{RUNBOOK,MANIFEST}.md`.
- Canary theo job mới: 5% → 25% → 50% → 100%, mỗi nấc so KPI với baseline S0; rollback drill trước 100%.

### S9 — Cleanup (3–4 ngày, sau cửa sổ ổn định)

- Xóa orchestration legacy trong `main.py`, `GeminiJSONClient`/`OllamaJSONClient` cũ, cờ `legacy`.
- Cập nhật `ARCHITECTURE.md`; full regression + dead-code scan.

## 4. Quy ước làm việc

- Mỗi sprint: branch riêng, PR nhỏ, cập nhật `DEV/langchain-langgraph-migration/IMPLEMENTATION.md` và `HANDOFF.md`.
- Mặc định mọi cờ mới ở trạng thái `legacy/off` → merge vào `main` không đổi hành vi production.
- Môi trường test: Python local hiện thiếu dependency runtime (đã gặp với `cryptography`), nên test chạy trong
  container `vidtrans` (image production) hoặc tạo venv từ `requirements*.txt`. Cần chốt cách này ở S0.
- Không thêm dependency ngoài danh sách đã pin ở S0 khi chưa cập nhật ADR.

## 5. Rủi ro cho dev

| Rủi ro | Sprint | Giảm thiểu |
|---|---|---|
| Refactor `process_video` gây regression âm thầm | S5 | Refactor thuần trước, so checksum output, PR nhỏ |
| Lệch trạng thái giữa graph checkpoint và `StageCheckpointStore` | S1, S6 | Graph chỉ giữ ref/checksum; consistency test trong recovery matrix |
| Xung đột version Pydantic/FastAPI khi thêm LangChain | S0 | Spike + pin chính xác trước khi viết code |
| Image tăng kích thước, build arm64 lỗi | S0 | Đo ở G0, chỉ cài provider package cần dùng |
| Publish trùng khi resume | S7 | Write-ahead + idempotency + fault injection |
| Ước lượng S5–S6 vượt do `main.py` lớn | S5–S6 | Đo tốc độ sau 2 stage đầu, cập nhật lại kế hoạch |

## 6. Approval record

| Phiên bản | Người duyệt | Thời điểm | Nội dung |
|---|---|---|---|
| `0.1` |  |  |  |

Sau khi duyệt, bước đầu tiên là S0: viết URD/OPEN_QUESTIONS, thu baseline và chạy dependency/checkpointer spike.
Chưa viết code production trước khi G0 pass.
