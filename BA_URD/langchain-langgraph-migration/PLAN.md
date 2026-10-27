# Kế hoạch chuyển đổi sang LangChain/LangGraph

- Feature: `langchain-langgraph-migration`
- Phiên bản plan: `0.2` (đóng blocker, xem mục 8; kế hoạch dev: `DEV/langchain-langgraph-migration/DEVELOPMENT_PLAN.md`)
- Trạng thái: `IN_REVIEW`
- Ngày: `2026-10-27`
- Nguồn yêu cầu: yêu cầu chuyển đổi toàn bộ quy trình dựa trên kiến trúc hiện tại để tối ưu hệ thống.
- Phạm vi đã thực hiện: phân tích và lập plan; chưa coding, chưa thêm dependency, chưa đổi DB/deploy.

## 1. Kết quả mong muốn

1. Một `GraphRegistry/WorkflowRunner` thống nhất cho các background job.
2. Ba graph chính: video translation, comic/animation, social publishing.
3. Một model gateway LangChain cho Gemini/Ollama, structured output và fallback.
4. Hai lớp checkpoint được phân định rõ: graph state và media artifact.
5. API/job/event hiện hữu tương thích; rollout bằng feature flag, canary và rollback được.
6. Bộ test có unit, graph routing, contract, recovery/fault injection, benchmark và security.

## 2. Quyết định kiến trúc đề xuất để review

| ID | Quyết định đề xuất | Lý do |
|---|---|---|
| ADR-01 | LangGraph chỉ điều phối; domain/pipeline service vẫn là Python typed service | Giữ tính xác định, testability và hiệu năng |
| ADR-02 | LangChain chỉ áp dụng tại ranh giới LLM | Không ép OCR/FFmpeg/TTS vào abstraction không phù hợp |
| ADR-03 | `JobStore` là lifecycle source of truth | Giữ tương thích API, queue và ownership |
| ADR-04 | `StageCheckpointStore` tiếp tục quản lý artifact; graph checkpointer chỉ quản lý state nhỏ | Tránh binary/PII và tránh mất cache checksum hiện có |
| ADR-05 | Migration theo strangler, legacy và graph cùng tồn tại tạm thời | Rollback nhanh và giảm blast radius |
| ADR-06 | Pilot từ comic storyboard | Đã có boundary tốt, LLM/repair/fallback rõ, ít ảnh hưởng video core |
| ADR-07 | LangSmith/tracing là opt-in, metadata allow-list | Đáp ứng privacy và triển khai offline/local |

## 3. Roadmap thực hiện

| Bước | Đầu vào | Việc làm | File đầu ra dự kiến | Cách kiểm chứng | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| 0. Baseline | Code/test/metric hiện tại | Chốt scope, KPI, inventory workflow, dependency spike ARM64/x86_64 | `URD.md`, `OPEN_QUESTIONS.md`, benchmark baseline | Review URD; build/test spike | Duyệt plan | NOT_STARTED |
| 1. ADR & contract | URD/KPI | Chốt state schema, error taxonomy, graph versioning, checkpoint ownership, tracing policy | `DEV/.../DESIGN.md`, ADRs | Architecture review, threat review | Bước 0 | NOT_STARTED |
| 2. Foundation | ADR | Thêm dependency pin; `GraphRegistry`, runner adapter, context, progress/event adapter, no-op tracing | `backend/workflows/*`, unit tests | Unit + serialization + cancellation tests | Bước 1 | NOT_STARTED |
| 3. LangChain gateway | Provider contracts hiện hữu | Bọc Gemini/Ollama; prompt template; structured output; retry/fallback mapping | `backend/infrastructure/ai/*`, contract tests | Golden/contract tests so với client cũ | Bước 2 | NOT_STARTED |
| 4. Pilot storyboard | Comic storyboard | Tạo `storyboard_subgraph`, repair loop, heuristic fallback, shadow compare | `backend/workflows/comic/*` | Golden dataset, route test, output schema/coverage | Bước 3 | NOT_STARTED |
| 5. Comic/animation | Comic runner | Chuyển references, scene/shot fan-out, voice, animation QC/fallback, export | comic graph/subgraphs | Cache reuse, interrupt/resume, fault injection | Bước 4 | NOT_STARTED |
| 6. Video translation | `process_video` + checkpoint | Tách node preflight/source/OCR-ASR/reconcile/translate/layout/TTS/mix/render/finalize | `backend/workflows/video/*`; `main.py` mỏng hơn | Regression 3 modes, restart at every boundary, perf benchmark | Bước 2-3, comic canary ổn định | NOT_STARTED |
| 7. Social publish | Publisher flows | Graph hóa publish/poll/reconciliation; bảo đảm idempotency | `backend/workflows/publishing/*` | Simulated timeout/409/429/5xx; no duplicate publish | Bước 2 | NOT_STARTED |
| 8. Observability/eval | Graph chạy canary | Node metrics, trace redaction, datasets, offline/online evaluator tùy chọn | dashboards/config/docs | Không rò dữ liệu; trace correlation; evaluator baseline | Bước 3-7 | NOT_STARTED |
| 9. Canary rollout | Legacy + graph | Feature flag theo job/tenant, 5%→25%→50%→100%, drain/rollback | runbook, manifest | KPI gate từng nấc, rollback drill | Test pass | NOT_STARTED |
| 10. Cleanup | 100% ổn định | Xóa orchestration legacy và HTTP LLM client cũ, cập nhật architecture | code/docs/deprecation note | Full regression, dead-code scan | Hết thời gian quan sát | NOT_STARTED |

## 4. Lát cắt triển khai chi tiết

### Phase A — BA/URD và baseline

- Liệt kê mọi `job_action`, scheduler, side effect và trạng thái public.
- Chốt in-scope/out-of-scope; lập bảng AS-IS → TO-BE cho từng flow.
- Thu baseline: completion/failure/cancel rate, latency/RTF theo stage, retry count, cache hit,
  chi phí/token (nếu có), memory/CPU/GPU.
- Tạo golden dataset đã ẩn danh cho storyboard và translation.
- Chốt NFR, rollback SLO và tiêu chí canary.

**Definition of Done:** URD được người dùng duyệt; câu hỏi chặn về scope, telemetry và DB checkpoint được đóng.

### Phase B — Nền tảng graph

- Package dự kiến:

```text
backend/workflows/
  contracts.py          # typed state, envelope, errors
  registry.py           # graph name/version -> compiled graph
  runner.py             # adapter với JobScheduler/JobService
  context.py            # trusted runtime dependencies
  progress.py           # graph event -> job/event adapter
  checkpointing.py      # PostgreSQL graph checkpoint config/migration
  video/
  comic/
  publishing/
```

- Không cho node import FastAPI request hoặc đọc identity từ graph input.
- Mọi node nhận `WorkflowContext` đã xác thực và trả typed state update.
- Cancellation check trước/sau tác vụ dài; giữ semaphore Whisper/OCR/AI hiện tại.
- Graph version pin trong persisted request và fingerprint.

**Definition of Done:** graph mẫu có thể queue, cancel, crash/resume, emit progress và rollback về legacy.

### Phase C — LangChain model gateway

- Chuẩn hóa model factory theo provider/model; timeout và retry policy ở một chỗ.
- Dùng Pydantic structured output cho storyboard/metadata; vẫn giữ validator nghiệp vụ và heuristic fallback.
- Adapter error sang taxonomy chung, không lộ raw provider body.
- Callback/tracing dùng allow-list metadata; nội dung prompt/output tắt mặc định.
- Contract test đảm bảo provider mới không làm thay đổi UI-visible error/status ngoài mapping đã duyệt.

**Definition of Done:** storyboard pilot đạt schema-valid rate và coverage không thấp hơn baseline; có kill switch về client cũ.

### Phase D — Comic/animation graph

- Storyboard thành subgraph với repair loop hữu hạn.
- Scene/shot dùng parallel branch có giới hạn; artifact key giữ như hiện tại.
- Thêm optional review interrupt sau storyboard, không bắt buộc bật ở rollout đầu.
- Animation QC/retry/2.5D fallback thành conditional edges rõ ràng.

**Definition of Done:** output/cache semantics tương thích; resume không render lại asset hợp lệ; edit một shot chỉ invalid đúng downstream.

### Phase E — Video translation graph

- Trước hết refactor `process_video` thành service/node mà chưa đổi behavior.
- Dựng graph từ các node deterministic; OCR/ASR fan-out/fan-in.
- Mỗi node dùng `StageCheckpointStore`; graph state chỉ giữ ref/checksum.
- Ba mode là conditional edges; nhánh TikTok content/publish tách khỏi critical render path.
- Giữ response, progress label và event contract; field mới chỉ additive/versioned.

**Definition of Done:** test toàn bộ mode/provider/language; kill/restart tại từng boundary; output checksum/semantic equivalence theo bộ mẫu; overhead đạt NFR.

### Phase F — Social publishing graph

- Chuẩn hóa provider lifecycle: prepare → upload/container → poll → publish → reconcile.
- Ghi state trước và sau remote side effect; retry chỉ khi error class cho phép.
- Bảo toàn tenant connection lookup và encrypted credential boundary.

**Definition of Done:** fault injection không tạo bài trùng; reconciliation phục hồi được sau restart; lỗi publish không làm mất video.

### Phase G — rollout và cleanup

- Canary theo tenant/job mới; job đang chạy giữ engine/version cũ.
- Gate rollout bằng KPI, không chỉ bằng unit test.
- Diễn tập rollback và resume graph version cũ trước 100%.
- Chỉ xóa legacy sau cửa sổ ổn định được duyệt.

## 5. Chiến lược test

| Nhóm | Nội dung bắt buộc |
|---|---|
| Unit | Node, state reducer, route, error mapping, redaction, idempotency key |
| Contract | API/OpenAPI, job payload/event, provider adapters, artifact schema |
| Graph | Happy path, mỗi conditional edge, repair/fallback, cancellation, interrupt/resume |
| Recovery | Kill trước/sau từng node; corrupt/missing artifact; stale graph version |
| Fault injection | Timeout, rate limit, 5xx, invalid JSON, Ollama unavailable, publisher ambiguity |
| Security | Cross-tenant job/artifact/checkpoint, forged owner, trace leak, path traversal |
| Regression | 3 video modes, 8 languages, OCR/ASR hybrid, comic/static/animated, all publishers |
| Performance | Orchestration overhead, RTF, parallelism, DB/checkpoint writes, memory |
| Evaluation | Story coverage/schema/factual consistency; translation preservation/target language |

Không dùng LLM-as-judge làm bằng chứng duy nhất. Schema, checksum, coverage, language/script,
idempotency và API compatibility phải có evaluator xác định.

## 6. Kế hoạch dữ liệu và deploy

- Có thể cần bảng/schema riêng cho LangGraph checkpointer; chỉ tạo migration sau khi chốt package/version
  và cơ chế PostgreSQL cụ thể.
- Không migration artifact cũ hàng loạt. Legacy job giữ engine cũ; job graph mới tạo graph checkpoint mới.
- Cấu hình dự kiến (chỉ là contract, chưa thêm vào config): graph engine mode, graph version, tracing enabled,
  tracing endpoint/project, sampling, redaction mode. Secret vẫn qua secret manager/environment.
- Build matrix phải kiểm tra Linux `amd64` và `arm64`, Python version hiện tại, Pydantic/FastAPI compatibility.
- Không cần Camunda; LangGraph là runtime orchestration trong ứng dụng. n8n integration tiếp tục nhận event.

## 7. Rollout gates đề xuất

| Gate | Điều kiện tối thiểu |
|---|---|
| G0 | Dependency/build spike pass, không tăng image/runtime ngoài ngưỡng đã duyệt |
| G1 | Storyboard shadow đạt schema/coverage baseline |
| G2 | Comic canary không regression cache/resume/output |
| G3 | Video graph fault-recovery pass mọi boundary và 3 mode |
| G4 | Publish graph chứng minh no-duplicate dưới fault injection |
| G5 | Canary 5/25/50% đạt KPI, rollback drill pass |
| G6 | 100% ổn định hết cửa sổ quan sát trước cleanup |

## 8. Rủi ro, blocker và hướng xử lý

| Loại | Nội dung | Xử lý đề xuất |
|---|---|---|
| RESOLVED 2026-10-27 | Phạm vi “tất cả quy trình” | Chốt: media/AI/publish; auth/billing/admin/CRUD/retention ngoài phase đầu |
| RESOLVED 2026-10-27 | Chính sách LangSmith/telemetry | Chốt: no-op mặc định, LangSmith SaaS opt-in với metadata allow-list |
| RESOLVED 2026-10-27 | Schema checkpoint DB | Chốt: cho phép schema riêng sau spike + ADR |
| RESOLVED 2026-10-27 | Human review bắt buộc | Chốt: không bắt buộc; interrupt dựng sẵn, tắt mặc định |
| RISK | Graph + artifact checkpoint lệch trạng thái | Consistency contract và recovery matrix |
| RISK | Publish side effect trùng | Write-ahead + idempotency + reconciliation |
| RISK | Refactor `main.py` lớn | Tách service trước, graph hóa sau, giữ regression suite |
| RISK | Model output thay đổi | Golden dataset, shadow comparison, kill switch |

## 9. File dự kiến theo quy trình delivery

```text
BA_URD/langchain-langgraph-migration/
  ANALYSIS.md
  PLAN.md
  URD.md
  OPEN_QUESTIONS.md
  REVIEW.md
  TRACEABILITY.md
  HANDOFF.md
DEV/langchain-langgraph-migration/
  DESIGN.md
  IMPLEMENTATION.md
TEST/langchain-langgraph-migration/
  TESTCASES.md
  TEST_REPORT.md
deploy/langchain-langgraph-migration/
  MANIFEST.md
  RUNBOOK.md
  sql/                 # chỉ khi checkpoint schema được duyệt
```

Source/test thực tế tiếp tục nằm trong `backend/` và `backend/tests/`, không sao chép vào `DEV/`.

## 10. Tự review plan

| Tiêu chí | Kết quả | Ghi chú |
|---|---|---|
| Đủ nguồn kiến trúc hiện tại | PASS | Đã đối chiếu architecture, scheduler/service, main pipeline, comic, provider, checkpoint |
| Scope rõ | PARTIAL | Còn câu hỏi về auth/billing/CRUD và telemetry |
| Compatibility | PASS ở mức kế hoạch | Có legacy runner, pin version, API compatibility gate |
| Security/secret/tenant | PASS ở mức kế hoạch | Trusted context, allow-list trace, cross-tenant tests |
| Testability | PASS | Có node/graph/contract/recovery/fault/eval/perf |
| Rollback | PASS ở mức kế hoạch | Canary + engine pin + legacy fallback; cần runbook ở phase deploy |
| Khả năng triển khai | PASS có điều kiện | Cần dependency/checkpointer spike trước khi chốt design |

## 11. Approval record

| Version/phạm vi | Người duyệt | Thời điểm | Nội dung xác nhận |
|---|---|---|---|
| `0.1` |  |  |  |

## 12. Điểm review cần người dùng xác nhận

1. Duyệt hướng: LangGraph cho orchestration, LangChain chỉ cho LLM boundary.
2. Phase đầu gồm video translation + comic/animation + social publishing; auth/billing/admin/CRUD/retention chưa graph hóa.
3. PostgreSQL vẫn là nguồn sự thật và cho phép nghiên cứu schema checkpointer riêng.
4. LangSmith SaaS mặc định tắt; chỉ bật khi có phê duyệt dữ liệu/telemetry.
5. Duyệt thứ tự pilot comic → video → publishing và cơ chế legacy/canary.

Sau khi plan `0.1` được duyệt, bước tiếp theo là viết URD và làm dependency/checkpointer spike;
chưa coding production trước khi đóng các blocker trên.
