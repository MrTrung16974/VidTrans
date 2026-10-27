# Phân tích chuyển đổi orchestration sang LangChain/LangGraph

- Feature: `langchain-langgraph-migration`
- Phiên bản: `0.1-draft`
- Ngày phân tích: `2026-10-27`
- Trạng thái: `DRAFT`
- Phạm vi phiên này: phân tích kiến trúc và lập kế hoạch; chưa thay đổi source code/dependency/runtime.

## 1. Vấn đề và mục tiêu

VidTrans đã có nền tảng tốt của một modular monolith: API, application service, domain,
provider, pipeline và persistence được phân lớp; job được lưu bền vững trong PostgreSQL;
artifact có fingerprint/checksum và có thể resume. Tuy nhiên, điều phối video chính vẫn tập
trung trong `backend/main.py`, còn các quy trình comic, animation và social publishing dùng
các cơ chế điều phối riêng. Retry, fallback, progress, checkpoint và error routing vì vậy bị
phân tán, khó quan sát toàn tuyến và tốn công khi thêm workflow mới.

Mục tiêu là chuẩn hóa lớp orchestration bằng LangGraph và chuẩn hóa phần tương tác LLM bằng
LangChain, nhưng không biến các thuật toán xác định như OCR, ASR, FFmpeg, TTS, persistence hay
authorization thành agent tự trị.

### Mục tiêu đo được

1. Mỗi loại job nền có graph, state schema và version rõ ràng; không còn một hàm orchestration
   video nguyên khối trong `backend/main.py`.
2. Job có thể retry/resume từ node hợp lệ mà không lặp side effect đã hoàn tất.
3. Provider LLM (Gemini/Ollama và provider tương lai) dùng một contract LangChain thống nhất,
   structured output được kiểm tra bằng Pydantic.
4. API `/api/v1`, WebSocket/event, job payload, artifact hiện hữu và tenant isolation giữ tương thích.
5. Có trace theo `job_id`, `tenant/user`, graph version, node, provider và latency nhưng không ghi
   transcript, prompt, token, secret hay PII thô mặc định.
6. Canary đạt tỷ lệ hoàn tất, thời gian xử lý và chi phí không kém baseline đã thống nhất trước rollout.

## 2. Bằng chứng kiến trúc hiện tại

| Hạng mục | Trạng thái xác nhận | Bằng chứng |
|---|---|---|
| Kiến trúc | Modular monolith, API/pipeline/worker tách contract | `ARCHITECTURE.md` |
| Queue/worker | PostgreSQL claim + local bounded worker pool | `backend/application/job_scheduler.py`, `backend/application/job_service.py` |
| Video orchestration | Luồng dài, state/progress/checkpoint nằm trong `main.py` | `backend/main.py::process_video`, `resume_process_video` |
| Checkpoint artifact | Fingerprint, checksum, dependency checksum theo job | `backend/infrastructure/stage_checkpoint.py` |
| Comic orchestration | Đã tách thành application runner + pipeline/provider | `backend/application/comic_service.py` |
| LLM | HTTP client tự viết cho Gemini/Ollama, JSON parse/repair thủ công | `backend/infrastructure/llm_providers.py`, `backend/pipeline/comic_storyboard.py` |
| Translation | Provider/fallback/retry riêng, có local và remote model | `backend/infrastructure/translation_providers.py`, `backend/pipeline/translation.py` |
| Social publishing | Scheduler/application flow riêng, có reconciliation | `backend/application/social_publishing.py` |
| Workflow ngoài hệ thống | Có n8n event integration, không phải engine nội bộ | `backend/infrastructure/workflow_events.py`, `docs/n8n-integration.md` |

## 3. AS-IS

```text
FastAPI routes
  -> JobService/JobStore (PostgreSQL)
  -> JobScheduler -> resume_process_video dispatcher
       -> process_video (orchestration + progress + checkpoint + domain calls)
       -> ComicJobRunner (orchestration riêng)
       -> publish scheduler/runner (orchestration riêng)

LLM calls
  -> custom urllib Gemini/Ollama clients
  -> prompt string + JSON extraction + Pydantic validation + bounded repair
```

Điểm mạnh cần giữ:

- Hợp đồng API và job lifecycle đã ổn định.
- Artifact checkpoint hiện tại kiểm tra cả fingerprint, checksum và dependency; đây là lớp bảo vệ
  media mà graph checkpointer không thay thế.
- Các pipeline OCR/ASR/translation/layout/TTS/render phần lớn đã là hàm/service có test.
- Cancellation, quota, tenant ownership và publisher idempotency đã có semantics riêng.

Khoảng trống:

- State workflow không có schema/version trung tâm.
- Điều kiện nhánh và retry nằm rải trong imperative code.
- Không có trace xuyên graph thống nhất.
- Provider LLM và structured output bị lặp lại.
- Job checkpoint và workflow checkpoint chưa có mô hình liên kết chính thức.

## 4. TO-BE đề xuất

```text
FastAPI/API contracts (giữ nguyên)
  -> JobApplicationService (giữ quyền sở hữu, quota, lifecycle)
  -> WorkflowRunner/GraphRegistry
       -> video_translation_graph
       -> comic_generation_graph
            -> storyboard_subgraph
            -> media_generation_subgraph
       -> social_publish_graph

LangGraph node
  -> deterministic domain/pipeline service (OCR, ASR, translate, TTS, FFmpeg...)
  -> LangChain model gateway khi thực sự cần LLM
  -> ArtifactCheckpointStore cho file/bundle media
  -> WorkflowCheckpointer cho graph state nhỏ, versioned
  -> Progress/Event adapter -> JobService/WebSocket/n8n
```

### Ranh giới framework

**Dùng LangGraph cho:** state machine, conditional routing, fan-out/fan-in, retry policy,
interrupt/resume để người dùng review storyboard/subtitle, và subgraph tái sử dụng.

**Dùng LangChain cho:** chat model adapters, prompt templates, structured output, runnable config,
callbacks/tracing và model fallback có kiểm soát.

**Không chuyển thành agent:** authentication/authorization, quota, DB transaction, URL allow-list,
OCR/ASR, subtitle timing/layout, TTS synthesis, FFmpeg/render, checksum/fingerprint, publisher side
effect. Chúng là deterministic nodes/tools do graph gọi với input typed.

### Hai lớp persistence

- `JobStore` tiếp tục là nguồn sự thật cho lifecycle và API-visible status.
- `StageCheckpointStore` tiếp tục là nguồn sự thật cho artifact media lớn và cache hợp lệ.
- LangGraph checkpointer chỉ lưu graph state nhỏ, con trỏ artifact, node status và interrupt state.
- `thread_id = job_id`; mọi record phải ràng buộc owner/tenant từ server context, không nhận từ model/client.
- Không nhét binary, transcript đầy đủ hoặc credential vào graph state/checkpoint/tracing.

## 5. Mapping quy trình sang graph

### 5.1 Video translation graph

```text
START -> validate_job -> resolve_source -> preflight
  -> [OCR || ASR] -> reconcile -> segment/filter -> translate
  -> subtitle_layout
  -> mode router
       mode 1: preserve_audio
       mode 2/3: voice_route -> TTS -> audio_mix
  -> render -> social_content(optional) -> publish(optional) -> finalize -> END
```

- OCR/ASR là fan-out/fan-in có semaphore hiện hữu.
- Node đọc artifact checkpoint trước khi tính toán.
- Publish là subgraph riêng để giữ idempotency/reconciliation và không làm hỏng artifact video.
- Human review subtitle là phase sau, thêm bằng `interrupt` khi sản phẩm quyết định bật editor.

### 5.2 Comic/animation graph

```text
START -> load_story -> storyboard_subgraph -> save/editable_storyboard
  -> review interrupt (configurable)
  -> character_references -> scene/shot fan-out
       -> keyframe -> voice -> animation -> QC -> retry/fallback
  -> concat/export -> finalize -> END
```

- `storyboard_subgraph`: chunk -> model structured output -> validate -> bounded repair -> heuristic fallback.
- Cache nội dung/asset hiện hữu của `ComicProject` vẫn giữ nguyên.
- AI motion budget và semaphore vẫn do service xác định, không để model quyết định.

### 5.3 Social publishing graph

```text
START -> authorize/resolve_connection -> validate_media -> create_upload/container
  -> poll/status -> reconcile -> publish -> persist_receipt -> END
```

- Side effect node cần idempotency key và write-ahead checkpoint trước lời gọi remote.
- Trạng thái `NEEDS_RECONCILIATION` là nhánh workflow chính thức, không retry mù.

### 5.4 Các quy trình không cần graph riêng ở phase đầu

- Login/OAuth callback, admin, billing, CRUD project/series, retention và download artifact giữ ở
  application service/scheduler hiện tại.
- Retention có thể thành graph sau này chỉ nếu cần approval hoặc workflow nhiều bước; hiện chưa có lợi ích đủ lớn.

## 6. State và contract đề xuất

- `WorkflowEnvelope`: `schema_version`, `graph_name`, `graph_version`, `job_id`, `owner_user_id`,
  `correlation_id`, `request_ref`, `current_stage`, `attempt`, `warnings`, `error_code`.
- `VideoWorkflowState`: source metadata, language decisions, mode, artifact refs/checksums, stage results
  dạng nhỏ; không chứa binary.
- `ComicWorkflowState`: settings snapshot, storyboard revision/ref, selected scene/shot, asset refs,
  AI budget usage.
- `PublishWorkflowState`: platform, connection ref, media checksum/ref, idempotency key, remote receipt,
  reconciliation state.
- Node output là typed partial update; domain exceptions được map về taxonomy chung:
  `TRANSIENT`, `RATE_LIMIT`, `INVALID_INPUT`, `REVIEW_REQUIRED`, `CANCELLED`, `PERMANENT`,
  `NEEDS_RECONCILIATION`.

## 7. Tương thích và rollout

- Thêm cờ chọn engine ở cấp server/job: `legacy | graph_shadow | graph` (tên cấu hình chính thức cần chốt).
- `graph_shadow` chỉ chạy các node không side effect hoặc replay từ artifact/dataset; tuyệt đối không render/publish lần hai.
- Dual-read không dual-write artifact; graph mới dùng cùng contract và thư mục job, với graph version trong fingerprint.
- Job đã bắt đầu bằng engine nào phải hoàn tất/retry bằng engine đó; không chuyển engine giữa chừng.
- Rollback chỉ đổi job mới về legacy; job graph đang chạy được drain hoặc resume bằng version đã pin.
- Xóa legacy orchestration chỉ sau ít nhất một chu kỳ retention/canary ổn định và có bằng chứng rollback.

## 8. NFR và tiêu chí nghiệm thu kiến trúc

- **Reliability:** crash tại mọi node boundary có thể resume; node side effect không tạo bản trùng.
- **Performance:** overhead orchestration mục tiêu < 2% tổng thời gian job media; OCR/ASR parallelism không giảm.
- **Security:** deny-by-default; graph không tự cấp quyền; trace/checkpoint không chứa secret/PII/transcript thô.
- **Observability:** trace liên kết được `job_id` -> graph run -> node -> artifact checksum, có redaction.
- **Testability:** node thuần test độc lập; graph route test không cần model/network; golden dataset cho LLM.
- **Compatibility:** OpenAPI và payload status hiện tại không đổi ngoài field additive đã version hóa.
- **Operability:** có dashboard metric theo graph/node/provider và runbook rollback.

## 9. Rủi ro

| Rủi ro | Mức | Kiểm soát |
|---|---:|---|
| Trùng hai cơ chế checkpoint | Cao | Tách graph-state và artifact-state; định nghĩa nguồn sự thật, consistency test |
| Side effect publish bị lặp khi resume | Cao | Idempotency key, write-ahead state, reconciliation node, fault-injection test |
| Graph state chứa dữ liệu lớn/nhạy cảm | Cao | Chỉ lưu reference/checksum, redaction callback, test serialization |
| Mất tương thích job cũ | Cao | Pin `engine_version/graph_version`, legacy runner song song |
| Tăng dependency/image size hoặc xung đột Pydantic | Trung bình | Spike dependency riêng, lock version, build ARM64/x86_64 |
| LangChain adapter không phủ hết behavior custom | Trung bình | Port theo contract, contract tests trước khi thay provider |
| Observability SaaS làm lộ dữ liệu | Cao | Tracing opt-in; self-host/no-op exporter; metadata allow-list |
| Chuyển tất cả cùng lúc | Cao | Strangler migration theo vertical slice, canary và kill switch |

## 10. Giả định và câu hỏi mở

### ASSUMPTION

- PostgreSQL tiếp tục là persistence chính.
- Không thay đổi public API trong migration đầu.
- n8n vẫn là integration event consumer, không bị LangGraph thay thế trực tiếp.
- LangSmith là tùy chọn; hệ thống phải chạy đầy đủ khi không có SaaS tracing.

### OPEN

1. “Tất cả quy trình” có bao gồm OAuth/billing/admin/retention hay chỉ các job media/AI nền?
   Đề xuất: phase đầu chỉ job media/AI/publishing.
2. Có yêu cầu human approval bắt buộc cho storyboard/subtitle/publish không?
3. Cho phép LangSmith SaaS hay bắt buộc self-host/no external telemetry?
4. KPI baseline chính xác cho latency, completion rate, cost/job và recovery time là bao nhiêu?
5. Có chấp nhận schema/table riêng cho LangGraph checkpoint trong PostgreSQL không?

## 11. Kết luận

Chuyển đổi khả thi và phù hợp nhất theo hướng **LangGraph làm orchestration có trạng thái,
LangChain làm LLM abstraction**, không phải viết lại toàn hệ thống thành agent. Thứ tự ít rủi ro
nhất là dựng nền tảng chung, pilot comic storyboard, chuyển comic render, chuyển video translation,
sau đó social publish; giữ legacy runner đến khi canary và fault-recovery đạt chuẩn.
