# Kế hoạch tối ưu pipeline nhận diện, dịch và ghép video

- Feature: `media-pipeline-optimization`
- Version: `0.1`
- Trạng thái: `APPROVED`
- Phạm vi đề xuất: BA → DEV → TEST → chuẩn bị DEPLOY; chưa tự deploy production.
- Nguồn: `ANALYSIS.md` version 0.1, kiến trúc và source code hiện tại ngày 2026-10-16.

## 1. Kết quả mong muốn

Pipeline có benchmark định lượng, stage contract rõ, checkpoint/cache an toàn, retry theo stage, parallelism có giới hạn và quality gate cho ASR/dịch/TTS/render. Public API và ba processing mode hiện tại vẫn tương thích.

## 2. Definition of Done

- BA: URD chốt mode tối ưu, cache/privacy, SLO, acceptance criteria và phạm vi rollout.
- DEV: stage runner/manifest, metrics, checkpoint/cache và các tối ưu đã duyệt được triển khai theo lát nhỏ; lỗi TTS worker được propagate.
- TEST: có golden corpus hợp lệ, baseline trước/sau trên cùng hardware, unit/integration/restart/cache invalidation/quality regression đều có bằng chứng.
- DEPLOY: có feature flags, precheck capability, quota/cleanup, canary, rollback và dashboard/runbook; chưa coi là hoàn tất khi chỉ có benchmark local.

## 3. Kế hoạch tổng thể

| Bước | Đầu vào | Việc làm | File đầu ra dự kiến | Cách kiểm chứng | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---|
| 0. Review plan | ANALYSIS/PLAN 0.1 | Chốt ưu tiên latency/throughput, mode Fast/Balanced/Quality, cache scope/TTL và hardware mục tiêu | Cập nhật `ANALYSIS.md`, `PLAN.md` | Approval record có xác nhận thực tế | Người dùng review | COMPLETE |
| 1. BA/URD | Plan được duyệt | Viết UC/BR/AC/NFR, SLO, privacy/cache lifecycle và rollout rule | `BA_URD/media-pipeline-optimization/URD.md`, `OPEN_QUESTIONS.md`, `REVIEW.md`, `TRACEABILITY.md` | Review consistency và testability | Bước 0 | IN_REVIEW |
| 2. Baseline & observability | URD, corpus được phép dùng | Thêm stage timer/metrics an toàn; benchmark AS-IS theo mode/video/hardware | `DEV/.../DESIGN.md`, benchmark script/config, `TEST/.../BASELINE.md` | Chạy lặp lại ≥3 lần/case; lưu median/p95, RTF, quality | Corpus + hardware | IN_PROGRESS — instrumentation hoàn tất, benchmark chờ corpus/hardware |
| 3. P0 hardening | Baseline | Propagate TTS future errors; probe/capability rõ; atomic artifact write; test failure path | Source/test hiện hữu, `DEV/.../IMPLEMENTATION.md` | Unit/integration test lỗi từng cue/stage | Bước 2 | COMPLETE cho Lát A |
| 4. Stage runner & checkpoint | Stage contract | Tách orchestration, stage manifest/fingerprint, resume/invalidation và cleanup | `backend/application`/`pipeline`/`infrastructure` + tests | Kill/restart tại mỗi stage; chỉ chạy lại dependency cần thiết | Bước 3 | PARTIAL — kill sau TTS/restart PASS; chưa phủ mọi ranh giới stage và DF_001 duration đang OPEN |
| 5. ASR/OCR | Checkpoint + baseline | Audio reuse, conditional word timestamps, chunk retry, adaptive OCR/ASR, OCR streaming/chunking | ASR/OCR/media adapter + tests | WER/CER, coverage, RTF, temp disk và fallback regression | Bước 4 | PENDING |
| 6. Translation | Provider contract + cache | Provider capability, persistent cache, stable contextual batching, glossary/versioning | Translation provider/cache + tests | Cache/invalidation, rate-limit, cue mapping, quality gate | Bước 4 | PENDING |
| 7. TTS/audio | Stage cache + resource budget | Configurable bounded concurrency, TTS cache, cue retry, giảm FFmpeg/ffprobe, validate missing/overflow | TTS/audio modules + tests | Không mất cue; drift/overflow/latency/resource regression | Bước 4 | PENDING |
| 8. Render/resource scheduling | Capability baseline | Một-pass render, encoder selection, temp quota, resource-class scheduler | Media adapter/scheduler/config contract + tests | VMAF/SSIM hoặc quality gate, A/V sync, throughput batch | Bước 4, 5, 7 | PENDING |
| 9. UI/API progress | Stage/job contract | Hiển thị stage, cache/resume, cấu hình profile nếu được duyệt | API/frontend + tests | Compatibility và progress không lùi/nhảy sai | Bước 4–8 | PENDING |
| 10. Regression & canary | Tất cả lát | Chạy corpus, soak batch, cancel/retry/restart, so sánh trước/sau | `TESTCASES.md`, `TEST_REPORT.md`, `TRACEABILITY.md` | Đạt quality/performance gates, không NOT_RUN giả PASS | Bước 2–9 | PENDING |
| 11. Chuẩn bị rollout | Test report | Feature flags, precheck, cache cleanup, canary 10%→50%→100%, rollback | `DEPLOY/.../MANIFEST.md`, `RUNBOOK.md`, BM01/BM02 nếu được yêu cầu | Dry-run/runbook review; rollback dưới ngưỡng chốt | Bước 10 | PENDING |

## 4. Thứ tự lát triển khai đề xuất

### Lát A — Đo đúng và sửa lỗi nền

1. Định nghĩa `StageName`, `StageResult`, metric schema và correlation không chứa nội dung người dùng.
2. Thêm timer quanh ASR/OCR/translation/TTS/mix/render.
3. Sửa TTS futures để mọi exception được thu nhận; fail với danh sách cue lỗi đã sanitize.
4. Probe media/capability một lần và truyền metadata xuống stage.

Lát A có thể triển khai sớm vì rủi ro thấp, tạo bằng chứng cho các lát còn lại.

### Lát B — Checkpoint/resume

1. Manifest JSON versioned trong work directory, ghi atomically.
2. Fingerprint theo input + option + engine/model/provider/schema.
3. Reuse `transcript.json`, `translation.json`, subtitle layout và audio stage khi hợp lệ.
4. Invalidation theo dependency graph; retry giữ nguyên source artifact.
5. Cleanup TTL/quota và xóa cache liên quan khi người dùng xóa job nếu policy yêu cầu.

### Lát C — Nhận diện thích nghi

1. Audio normalization dùng chung và conditional word timestamps.
2. Language detection/confidence trước decode đầy đủ khi source auto.
3. ASR chunk checkpoint và retry chunk.
4. OCR stream/chunk, frame-change gate trước inference.
5. Fast/Balanced/Quality hoặc policy tương đương: ASR chỉ bổ sung vùng OCR yếu ở Balanced; Quality giữ full reconciliation.

### Lát D — Dịch và TTS có cache

1. Provider interface/capability và circuit state.
2. Cache dịch versioned + stable contextual batching.
3. TTS cache per cue, semaphore network/FFmpeg riêng và cue-level retry.
4. Giảm số ffprobe/subprocess sau khi benchmark xác nhận kết quả tương đương.

### Lát E — Render và throughput

1. Capability preflight và encoder profile.
2. Benchmark CPU/hardware encoder theo chất lượng và kích thước.
3. Resource-class scheduling, temp disk quota và backpressure.
4. Canary + rollback bằng feature flags độc lập cho cache, adaptive ASR/OCR và hardware encode.

## 5. Test strategy

### Correctness

- Unit: fingerprint, dependency invalidation, atomic manifest, TTL/quota, provider capability, exception propagation.
- Integration: kill/restart sau từng stage; cache corrupt/missing/version mismatch; cancel trong ASR/TTS/render.
- Compatibility: job cũ không manifest vẫn chạy full pipeline; API và ba mode giữ contract.
- Failure: provider 429/timeout, một cue TTS lỗi, FFmpeg filter/encoder thiếu, disk gần đầy, model unavailable.

### Quality

- ASR: WER cho ngôn ngữ dùng khoảng trắng, CER cho CJK/Thai khi phù hợp; tách theo noise/duration.
- OCR: precision/recall cue, CER, timing IoU và bbox error.
- Translation: stable cue mapping 100%, source fallback/review rate, bộ tham chiếu hoặc human rubric adequacy/fluency.
- TTS: missing cue = 0, p50/p95 drift, overflow rate và kiểm tra nghe mẫu đại diện.
- Render: duration/A-V delta, subtitle timing/position, codec compatibility, VMAF/SSIM hoặc review hình ảnh có tiêu chí.

### Performance

- Cùng host/image/model/provider condition, warm-up tách riêng khỏi steady state.
- Mỗi case chạy tối thiểu ba lần; báo median và p95, không chỉ một số tốt nhất.
- Đo cold cache, warm cache, retry sau ASR, retry sau translation và batch 1/2/N jobs.
- Báo RTF theo stage, peak RSS, CPU, temp disk, output size và cache hit.

## 6. Feature flags và rollback

Các flag cụ thể sẽ đặt tên trong DESIGN/CONFIG_CONTRACT, không sửa file secret. Tối thiểu cần tách:

- stage checkpoint/cache;
- adaptive OCR/ASR;
- translation persistent cache;
- TTS cache/concurrency mới;
- hardware encoder.

Rollback ưu tiên tắt từng flag về đường pipeline hiện tại. Manifest/cache mới phải additive; code cũ không được đọc nhầm artifact mới. Không rollback bằng cách xóa toàn bộ work/output hoặc database.

## 7. Rủi ro, blocker và hướng xử lý

| Mục | Mức | Xử lý |
|---|---|---|
| Chưa có runtime baseline và golden transcript/translation | Blocker cho việc tuyên bố đạt SLO, không chặn instrument/hardening | Chốt corpus và chạy Phase 0 trước tối ưu thuật toán. |
| Chưa biết cấu hình VPS production | Cao | Ghi profile host/image; không chốt concurrency/encoder bằng máy dev. |
| Cache/privacy chưa có policy | Cao | Mặc định cache per-job; cross-job cache chỉ bật sau khi chốt scope/TTL/delete. |
| Provider dịch/TTS bên ngoài biến động | Cao | Benchmark tách network, mock contract, metric retry/rate-limit; không so sánh hai lần ở điều kiện khác nhau. |
| Refactor `main.py` rộng | Trung bình | Adapter compatibility, lát nhỏ và test trước khi chuyển logic tiếp theo. |

## 8. Tự review plan

- Đủ nguồn: đã đối chiếu architecture, điều phối pipeline, ASR, OCR, translation, TTS, render, scheduler và test hiện có.
- Scope: tập trung nhận diện → dịch → ghép; không kéo tải nguồn/đăng mạng xã hội vào implementation.
- Compatibility: giữ public API, job cũ và ba processing mode; checkpoint là additive.
- Security/privacy: không đọc secret; metric không chứa transcript; cache có TTL/quota/delete policy trước khi reuse cross-job.
- Testability: mọi mục tối ưu gắn với baseline, quality gate và failure test.
- Rollback: feature flag theo lát; CPU/current path vẫn tồn tại trong canary.
- Gap: cần người dùng chốt ưu tiên latency/throughput, cache policy, profile chất lượng và hardware/corpus production.

## 9. Approval record

| Version/phạm vi | Người duyệt | Thời điểm | Nội dung xác nhận |
|---|---|---|---|
| 0.1 — BA → DEV → TEST → chuẩn bị DEPLOY | Người dùng | 2026-10-16 | “bắt đầu làm” |
| 0.3 — lựa chọn OCR + ASR độc lập theo cue | Người dùng | 2026-10-18 | “oki bắt đầu phát triển” |

Plan 0.1 đã được duyệt. Các quyết định chưa có dữ liệu production tiếp tục được quản lý trong `OPEN_QUESTIONS.md`; phần độc lập được phép triển khai trước.
