# Phân tích — AI tối ưu dựng video từ bài đăng ảnh

- Feature: `ai-photo-slideshow-director`
- Phiên bản: 0.1
- Trạng thái: DRAFT
- Ngày: 2026-11-05
- Nguồn yêu cầu: “Tối ưu dùng AI để chọn kiểu ghép bài ảnh thật tối ưu … và gợi ý chọn nhạc”.

## 1. Mục tiêu

Biến luồng tạo video từ bài đăng ảnh thành một “AI Director”: AI đọc caption và nội dung ảnh, đề xuất một phương án dựng hoàn chỉnh gồm phong cách, nhịp dựng, thời lượng, chuyển động camera, chuyển cảnh và nhạc; người dùng xem lý do, nghe thử nhạc, chỉnh lại và mới bấm tạo video.

Mục tiêu đo được cho MVP:

- Một lần gọi AI trả về phương án dựng hợp lệ trong tối đa 120 giây hoặc tự chuyển sang fallback.
- 100% giá trị AI trả về được validate/allowlist trước khi lưu hoặc đưa cho FFmpeg.
- Khi AI/model lỗi, người dùng vẫn tạo video bằng preset mặc định hiện tại.
- Web và app hiển thị cùng một phương án và gửi cùng contract tạo video.
- Renderer hỗ trợ thời lượng và chuyển động theo từng ảnh, không còn bắt buộc chia đều toàn bộ ảnh.

## 2. AS-IS

### CONFIRMED

- AI hiện đã đọc caption và tối đa 4 ảnh để gợi ý tối đa 5 bài nhạc trong `backend/application/music_suggestions.py`.
- Gợi ý nhạc chỉ được chọn từ `track_id` tồn tại trong thư viện; ID do model bịa được loại bỏ.
- Nếu model lỗi, hệ thống dùng heuristic theo từ khóa/cảm xúc caption.
- Renderer hiện giữ thứ tự ảnh gốc, chia đều thời gian, lặp bộ ảnh khi video dài, áp cùng một zoom-in nhẹ cho mọi ảnh và không có chuyển cảnh giữa các ảnh.
- Người dùng tự chọn độ dài video và một hoặc nhiều bài nhạc; web và app đã có nghe thử nhạc đã chọn.
- Endpoint gợi ý hiện tại có kiểm tra owner qua `job_for_request` và chỉ chạy khi job đang `awaiting_input`.

### Vấn đề

- Video dễ đều nhịp và đơn điệu vì tất cả ảnh dùng cùng thời lượng/chuyển động.
- AI chọn nhạc riêng lẻ, chưa tối ưu đồng thời nhạc với cấu trúc dựng.
- Không có “hook” ở 1–3 giây đầu, không xác định ảnh chủ đạo/kết, không tính nhịp theo mood hoặc BPM.
- Chưa có preview rõ ràng về phong cách và lý do AI lựa chọn trước khi render.

## 3. TO-BE đề xuất

### 3.1. AI Director trả về một `SlideshowPlan`

Plan chỉ dùng enum/giới hạn do server định nghĩa:

- `style`: `dynamic`, `cinematic`, `emotional`, `clean`, `travel`, `product`.
- `target_seconds`: gợi ý từ số ảnh và mood; người dùng vẫn có thể đổi.
- `image_order`: danh sách index ảnh, không được lặp/thiếu ngoài rule đã chốt.
- `slides[]`:
  - `image_index`.
  - `duration_seconds` trong khoảng an toàn.
  - `motion`: `slow_zoom_in`, `slow_zoom_out`, `pan_left`, `pan_right`, `pan_up`, `pan_down`, `static`.
  - `transition`: MVP giới hạn `cut`, `crossfade`, `fade`.
  - `transition_seconds` trong giới hạn server.
  - `role`: `hook`, `story`, `highlight`, `ending` để giải thích UI; renderer không tin field tự do.
- `music_suggestions[]`: track ID hợp lệ, điểm phù hợp, lý do, vị trí xếp hạng.
- `mood`, `summary`, `confidence`, `source`.

### 3.2. Cách AI chọn “kiểu ghép tối ưu”

- Nhận diện nhóm nội dung từ caption + ảnh mẫu: chân dung, du lịch, món ăn, sản phẩm, sự kiện, cảm xúc, before/after.
- Chọn ảnh mở đầu có khả năng tạo hook; ưu tiên ảnh rõ chủ thể/chất lượng cao.
- Chọn nhịp dựng theo mood:
  - năng động: thời lượng ngắn, cut/crossfade nhanh, motion đa dạng;
  - cảm xúc/cinematic: thời lượng dài hơn, zoom/pan chậm, fade mềm;
  - sản phẩm/clean: ít chuyển động, bố cục ổn định, nhạc không lấn át.
- Gợi ý nhạc trong cùng lần phân tích để mood, độ dài và nhịp dựng nhất quán.
- MVP không bắt buộc BPM extraction. Phase sau mới beat-sync theo waveform/BPM thật.

### 3.3. Trải nghiệm người dùng

- Nút `AI dựng tối ưu` trên web và app.
- Sau phân tích hiển thị thẻ:
  - tên phong cách + mood + độ dài đề xuất;
  - mô tả ngắn “Vì sao AI chọn kiểu này”;
  - danh sách 3–5 nhạc đề xuất, lý do và nút nghe thử;
  - nút `Áp dụng phương án`, `Phân tích lại`, và tùy chọn đổi nhạc/độ dài.
- Sau khi áp dụng, payload tạo video mang `slideshow_plan_id` hoặc snapshot plan đã được server validate.
- Người dùng luôn có thể bỏ AI và quay lại chế độ thủ công.

## 4. Phạm vi

### Trong phạm vi MVP

- Backend: phân tích ảnh/caption, contract plan, validator, heuristic fallback, quyền truy cập, cache kết quả theo job + phiên bản thư viện/prompt.
- Renderer: thứ tự ảnh, thời lượng theo slide, motion và transition allowlist.
- Web + app: gọi AI, hiển thị plan, nghe thử/chọn nhạc, áp dụng hoặc chỉnh lại.
- Test: parser/validator/fallback, authorization, renderer command, UI state và regression luồng thủ công.

### Ngoài phạm vi MVP

- Sinh ảnh/video mới bằng generative AI.
- Face recognition/nhận dạng danh tính.
- Tự động crop phá hủy ảnh gốc hoặc loại ảnh vĩnh viễn.
- Beat detection/BPM sync chính xác đến frame; đưa vào phase 2.
- Tự động đăng mạng xã hội sau khi dựng.

## 5. Acceptance criteria sơ bộ

- AC01: Chủ job có thể yêu cầu AI tạo plan khi job bài ảnh đang chờ lựa chọn; user khác nhận 404 như contract hiện tại.
- AC02: Plan chỉ chứa enum, index, duration và track ID hợp lệ; dữ liệu ngoài allowlist bị loại hoặc fallback.
- AC03: AI lỗi/timeout/JSON sai vẫn trả được plan heuristic an toàn, có `source=heuristic`.
- AC04: Web và app hiển thị phong cách, lý do, thời lượng, nhạc gợi ý và cho nghe thử.
- AC05: Người dùng áp dụng plan rồi vẫn sửa độ dài và nhạc trước khi tạo video.
- AC06: Renderer tạo đúng tổng thời lượng trong sai số tối đa 1 frame, có motion/transition theo plan và không crop mất nội dung chính theo mặc định.
- AC07: Luồng thủ công hiện tại vẫn hoạt động khi không dùng AI.
- AC08: Không gửi quá số ảnh/byte giới hạn tới model; không log raw ảnh, caption nhạy cảm hoặc nội dung model đầy đủ.
- AC09: Cùng một request đang xử lý không tạo nhiều lời gọi AI trùng; retry không làm phát sinh render hoặc quota ngoài ý muốn.

## 6. Hệ thống ảnh hưởng

- `backend/application/music_suggestions.py`: tách/reuse phần music ranking trong AI Director.
- Module mới dự kiến `backend/application/photo_slideshow_planning.py`.
- `backend/main.py`: endpoint phân tích và mở rộng `PhotoPostChoice`.
- `backend/infrastructure/tiktok_slideshow.py`: renderer theo plan và transition.
- `backend/infrastructure/llm_providers.py`: tái sử dụng `StoryLLM`, không hardcode provider.
- `web/js/app.js`, `web/index.html`, `web/assets/styles.css`.
- `mobile/lib/features/jobs/...`.
- `contracts/openapi.json`.
- Unit/integration tests liên quan photo post, slideshow và music suggestions.

## 7. Rủi ro và kiểm soát

- AI hallucination: JSON schema + parser allowlist + clamp + xác minh index/track ID + fallback.
- Latency/cost: dùng tối đa 4–6 ảnh đại diện, resize trước model, cache theo fingerprint; timeout 120 giây.
- Ảnh riêng tư: chỉ xử lý ảnh của đúng owner; không ghi raw ảnh vào log; tài liệu hóa provider boundary.
- Chất lượng không ổn định: lưu `prompt_version`, `planner_version`, `source` và plan snapshot để tái hiện.
- FFmpeg phức tạp/tốn CPU: MVP chỉ 3 transition và 7 motion; benchmark số ảnh tối đa trước rollout.
- Plan và thư viện nhạc lệch phiên bản: validate lại track khi áp dụng; yêu cầu chọn lại nếu file đã bị xóa.

## 8. Giả định và câu hỏi mở

- ASSUMPTION: AI chỉ đề xuất; người dùng phải bấm áp dụng/tạo video, không tự động tiêu quota.
- ASSUMPTION: Giữ ảnh gốc theo mặc định và dùng nền blur như renderer hiện tại.
- ASSUMPTION: MVP ưu tiên chất lượng dựng ổn định hơn số lượng hiệu ứng.
- OPEN Q01: Có cho AI thay đổi thứ tự ảnh hay chỉ giữ nguyên thứ tự bài đăng? Đề xuất: cho phép, nhưng UI hiển thị thứ tự và có nút “Giữ thứ tự gốc”.
- OPEN Q02: AI được phép bỏ ảnh kém chất lượng/trùng lặp không? Đề xuất MVP: không bỏ tự động; chỉ đánh dấu cảnh báo. Phase 2 mới cho phép với xác nhận.
- OPEN Q03: Độ dài video để AI tự chọn hoàn toàn hay giới hạn theo lựa chọn người dùng? Đề xuất: AI gợi ý, người dùng có quyền đổi.

