# Design — AI Photo Slideshow Director

- Plan/URD: 0.1, đã duyệt 2026-11-05.
- Câu hỏi chặn: không có; dùng ba mặc định đã duyệt.

## 1. Module và trách nhiệm

- `backend/application/photo_slideshow_planning.py`: domain constants, schema, AI prompt, parser/validator, heuristic fallback. Không phụ thuộc FastAPI/job store.
- `backend/main.py`: authz/state/entitlement, lấy file ảnh và thư viện nhạc, gọi planner; validate plan khi submit.
- `backend/infrastructure/tiktok_slideshow.py`: build/render planned slideshow; chỉ nhận dataclass/dict đã validate.
- Web/app: state loading/result/error; không tự suy diễn plan.

## 2. Contract và validation

- Request AI plan: target 5–600 giây; `preferred_style` allowlist hoặc `auto`.
- Parser map model JSON sang normalized dict. Ảnh phải xuất hiện đúng một lần; nếu thiếu/lặp/out-of-range thì fallback.
- Duration từng slide 1–8 giây; tổng được scale về target. Transition 0–0.8 giây và nhỏ hơn nửa duration hai slide kề.
- Music suggestion chỉ giữ ID có trong thư viện, tối đa 5, reason ≤160 ký tự, score clamp 0–1.
- Plan `version=1`; submit chỉ nhận version hỗ trợ.

## 3. Renderer

- Planned renderer tạo segment MP4 tạm cho từng ảnh bằng existing compose + zoompan/motion expressions, sau đó concat/crossfade/fade theo allowlist.
- MVP ưu tiên correctness: segment files trung gian, sau đó ghép; dễ test và tránh đưa expression model vào FFmpeg.
- Audio được loop/trim/fade như renderer hiện tại.
- Manual renderer không đổi.

## 4. Auth, transaction và idempotency

- Identity lấy từ session; `job_for_request` che object khác bằng 404.
- Tạo plan là read-only, không reserve quota. Không persistence trong MVP; client gửi snapshot lại và server validate.
- Submit giữ transaction/state transition hiện tại và snapshot plan trong `photo_choice`/`resume_request`.
- UI disable nút khi request đang chạy; endpoint có thể bị gọi lại nhưng không side effect ngoài cost model.

## 5. Lỗi và fallback

- Model/provider/JSON: heuristic plan, HTTP 200 với `source=heuristic`.
- Job/state/file/entitlement: HTTP 4xx, không gọi model.
- Plan submit sai: HTTP 400, không reserve quota.
- Render fail: job failure behavior hiện tại.

## 6. Test strategy

- Unit planner: valid/hallucinated/invalid/fallback/order/duration/music.
- API: owner/state/entitlement/model timeout/submit snapshot.
- Renderer: command/segments/order/motion/transition/audio/manual regression.
- Web/app: loading/apply/retry/manual/audio lifecycle.

## 7. Tradeoff

- Không cache server và không BPM sync trong MVP để tránh schema/persistence và audio-analysis scope; metrics/user feedback quyết định phase 2.
- Gửi plan snapshot thay vì plan ID giúp stateless, nhưng server phải validate lại đầy đủ.

