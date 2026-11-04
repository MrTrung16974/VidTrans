# Kế hoạch — qidian-chapter-import

- Version: 0.3
- Trạng thái: `IN_REVIEW` — P0, P1, P2 và phần tách tập của P3 đã làm (xem `ANALYSIS.md`, `HANDOFF.md`); chưa deploy
- Nguồn: yêu cầu trực tiếp ngày 04/11/2026: trong phần tạo truyện, cho phép tải trực tiếp chương truyện từ link Qidian.
- Tham khảo:
  - `hcoona/three` — `src/private/app/qidian-novel-downloader` (C#, Playwright).
  - voz.vn `t/cach-lay-text-tren-qidian.1109747`: đọc được trang 1. Trang 2–3 bị Cloudflare chặn (HTTP 403) khi đọc tự động, **chưa đọc**.

## 1. Mục tiêu

Ở form **Tạo truyện → video** (comic), người dùng dán link một bộ truyện, xem mục lục, chọn chương, rồi hệ thống tải nội dung các chương đó vào truyện. Người dùng không phải copy tay từng chương.

- **Nhiều trang truyện**, không chỉ Qidian. Qidian là adapter đầu tiên. Làm sau: **Fanqie** (`fanqienovel.com`) và **Jinjiang** (`www.jjwxc.net`). Các trang khác thêm dần sau đó.
- **Vượt 60.000 ký tự thì tự tách** thành nhiều tập của một series, cắt đúng ranh giới chương.
- **Text tiếng Trung được xử lý tự động**: LLM chia cảnh dịch luôn (xem §5).
- **Editor trở lên** được dùng tính năng nhập truyện. Đăng nhập tài khoản nguồn (tab Nguồn) vẫn do owner/admin quản lý.
- **Tài khoản phụ** dùng để đăng nhập các trang nguồn.

## 2. Ràng buộc bắt buộc

- **Chỉ lấy nội dung mà phiên đăng nhập hiện tại được xem hợp lệ**, giống repo tham khảo (`requirements.md` §1 và §7):
  - chương miễn phí: lấy toàn bộ;
  - chương VIP tài khoản đã mua: lấy nếu trang hiển thị text đọc được;
  - chương VIP chưa mua: chỉ có phần xem trước, gắn nhãn rõ ràng, không đưa vào truyện nếu người dùng không xác nhận.
- **Không vượt paywall, không giải mã font/DRM.** Theo voz trang 1, text chương trả phí khi copy ra bị rác (chống copy hoặc mã hóa font). Hệ thống phát hiện trường hợp này và báo "chương được bảo vệ, không lấy được text". Không làm bước giải mã.
- **Bản quyền:** truyện Qidian có bản quyền. Trước khi tải, UI hiện cảnh báo và yêu cầu người dùng xác nhận đã có quyền chuyển thể nội dung.
- **Không gây tải cho Qidian:** mỗi lần chỉ tải 1 chương, nghỉ ngẫu nhiên 5–12 giây giữa các chương, thử lại tối đa 3 lần có giãn cách (theo `RequestDelayPlanner` của repo).

## 3. Đã xác minh

| Kiểm tra | Kết quả |
|---|---|
| `curl` mục lục `qidian.com/book/1045928363/catalog/` từ máy local | HTTP 202, trang rỗng 209 B chỉ có `probe.js` (thử thách chống bot) |
| Cùng request từ VPS | Giống hệt: 202 + `probe.js` |
| Repo tham khảo | Dùng Playwright với profile riêng, đăng nhập thủ công bằng trình duyệt có giao diện, tải bằng headless |

**Kết luận:** không tải được bằng HTTP thường. Phải dùng Chromium thật. VidTrans đã có sẵn: `social-browser` (trang **Nguồn**, giờ luôn bật), điều khiển qua CDP/Playwright như `SocialBrowserAuthManager.resolve_facebook_video`.

## 4. Cách lấy dữ liệu (chuyển từ `PageScripts.cs`)

- **Nhận diện link:** `qidian.com/book/{bookId}`, `qidian.com/chapter/{bookId}/{chapterId}`, `m.qidian.com/book/{bookId}`, hoặc nhập thẳng `bookId` dạng số.
- **Mục lục:** mở `https://www.qidian.com/book/{bookId}/catalog/` rồi chạy script `CatalogJson`:
  - mỗi quyển là một thẻ `h3` có dạng `…·共XX章`; có chữ `VIP` mà không có `免费` thì là quyển VIP;
  - link chương có dạng `/chapter/{bookId}/{chapterId}/`;
  - số chữ của chương lấy từ thuộc tính `title` (`章节字数：N`);
  - chương cần mua được nhận ra qua icon `em.iconfont` trong dòng của chương.
- **Nội dung chương:** chạy script `ChapterContentJson`:
  - ưu tiên `span.content-text` (tránh dính số bình luận);
  - nếu không có thì thử `main p`, `.read-content p`, `#j_chapterContent p`, sau khi bỏ `.review*`;
  - chương chỉ có phần xem trước được nhận ra qua `需要订阅后才能阅读` hoặc `本章为付费章节`.
- **Phát hiện text bị bảo vệ (mới, repo không có):** coi là "được bảo vệ" và không dùng khi:
  - tỷ lệ ký tự Private Use Area (U+E000–F8FF) hoặc ký tự không hiển thị vượt ngưỡng, hoặc
  - độ dài text lệch quá xa so với `章节字数` trên mục lục.
- **Kiểm tra đăng nhập:** `#sign-in` không có class `hidden`, hoặc `#user-name` khác `用户名`. Trạng thái này chỉ ổn định sau 1–5 giây, nên poll tối đa 10 giây.
- **Trang riêng:** mỗi lần tải mở một page mới trong context hiện có rồi đóng lại, để không đụng trang người dùng đang xem ở tab Nguồn.

## 5. Thiết kế

### Backend

| Thành phần | Nội dung |
|---|---|
| `infrastructure/novel_sources/` (mới) | Lớp nguồn dùng chung. Interface `NovelSource`: `matches(url)`, `book_ref(url)`, `fetch_catalog(ref)`, `fetch_chapter(chapter)`, `login_platform`. Phần dùng chung: cache, giãn cách, khóa trình duyệt, phát hiện text bị bảo vệ, ghép text |
| `novel_sources/qidian.py` | Adapter đầu tiên, chuyển từ `PageScripts.cs` (§4) |
| Adapter sau: `fanqie.py`, `jjwxc.py` | Mỗi trang một file có selector riêng, kèm test bằng HTML mẫu. Trang chủ cả hai trả HTML bình thường (HTTP 200) khi gọi bằng HTTP ngày 04/11/2026, không có thử thách `probe.js` như Qidian; trang mục lục và chương chưa kiểm tra. Fanqie (bản web) và chương VIP Jinjiang được biết là dùng font mã hóa: nếu text bị bảo vệ thì chỉ báo lỗi, không giải mã (§2). Cần 1 link truyện mẫu mỗi trang trước khi làm |
| `social_browser_auth.py` | Thêm nền tảng `qidian` (domain `qidian.com`, `yuewen.com`) cho tab đăng nhập. Kiểm tra đăng nhập qua DOM (§4) vì chưa biết tên cookie phiên. Cần xác minh ở bước P0 |
| Cache `WORK_DIR/qidian-cache/{bookId}/` | `catalog.json` (TTL 24 giờ); `chapters/{chapterId}.json` gồm đoạn văn, số chữ, thời điểm tải. Tải lại khi số chữ trên mục lục đổi (quy tắc của repo) |
| Tác vụ nền | Executor riêng 1 worker. **Không** dùng hàng đợi job video, để không phải chờ sau job dịch video. Có tiến độ, hủy, giữ kết quả 1 giờ |
| API (dưới `/api/v1/comic/sources`, quyền editor trở lên) | `GET /catalog?url=…`: nguồn, tên truyện, tác giả, quyển, chương (id, tên, VIP, trạng thái truy cập, số chữ, đã cache chưa) · `POST /imports {url, chapter_ids[], series_id?}` trả `import_id` · `GET /imports/{id}`: tiến độ, từng chương (ok / preview / protected / failed), các tập dự kiến · `DELETE /imports/{id}`: hủy |
| Ghép text và tách tập | `第N章 Tên chương` + đoạn văn cách nhau một dòng trống. Gom chương liên tiếp vào một tập cho tới khi gần chạm `MAX_STORY_CHARS = 60_000`, không cắt giữa chương. Một chương dài hơn giới hạn thì tách theo câu (`_SENTENCE_END` đã có). Một tập: điền vào form như bình thường. Nhiều tập: tạo series mới (hoặc thêm vào series đã chọn) và xếp mỗi tập vào hàng đợi bằng `queue_episode` sẵn có |
| Tiếng Trung | Prompt chia cảnh đã yêu cầu LLM viết lời kể và hội thoại bằng ngôn ngữ đích (`comic_storyboard.py:131`), nên text tiếng Trung được dịch trong bước này. Khi không có LLM (đường heuristic), dịch trước từng chương bằng provider dịch hiện có. P0 sẽ kiểm tra chất lượng trên 1 chương thật |
| Contract | Cập nhật `contracts/openapi.json` (có test kiểm tra) |

### Web (form tạo truyện, `web/js/comic.js`)

1. Thêm ô **"Nhập từ link truyện"** và nút **Tải mục lục**.
2. Danh sách chương có checkbox và chọn nhanh khoảng "từ chương… đến chương…". Mỗi chương có nhãn **Miễn phí / VIP đã mua / Chỉ xem trước / Đã cache**, kèm bộ đếm số ký tự so với giới hạn 60.000.
3. Checkbox xác nhận quyền sử dụng nội dung, rồi nút **Tải chương**.
4. Thanh tiến độ "chương x/y", có nút hủy.
5. Khi xong: một tập thì điền ô *Nội dung truyện* và *Tên truyện*. Nhiều tập thì hiện bảng "Tập 1: chương 1–15, Tập 2: chương 16–30…" để xác nhận trước khi tạo series. Liệt kê các chương bị bỏ qua và lý do.
6. Nếu chưa đăng nhập trang nguồn: editor thấy thông báo nhờ owner/admin đăng nhập ở **Nguồn → tab tương ứng**.

### Mobile

Làm ở giai đoạn sau (P3), dùng chung API.

## 6. Các bước

| Bước | Việc | Kiểm chứng | Trạng thái |
|---|---|---|---|
| P0 Spike | Trên VPS, trong `social-browser`: mở mục lục, 1 chương miễn phí, 1 chương VIP; xem `probe.js` có vượt được không, IP VPS có bị chặn không, DOM có còn khớp `PageScripts.cs` không, cookie đăng nhập tên gì. Chạy chia cảnh trên 1 chương tiếng Trung. Chốt danh sách trang nguồn tiếp theo | Ghi kết quả vào `ANALYSIS.md` | Xong |
| P1 Backend | `novel_sources/`, tab đăng nhập, cache, tác vụ nền, API, contract | Unit test (nguồn giả) + chạy adapter với trang thật trên VPS | Xong |
| P2 Web | UI ở §5, tab Qidian ở trang Nguồn | Chạy thật trên VPS với 3 chương miễn phí | Code xong; chờ deploy để thử trên trình duyệt |
| P3 | Tách tập tự động vào series; mobile | Test tách tập | Tách tập xong; mobile chưa làm |
| P4 | Adapter Fanqie và Jinjiang | Test bằng HTML mẫu; thử thật chương miễn phí | Jinjiang xong (HTTP thường). Fanqie **không làm**: font mã hóa khoảng 51% ký tự (xem `ANALYSIS.md`) |
| HANDOFF | `HANDOFF.md`, deploy | Đối chiếu AC §7 | Chưa làm |

## 7. Tiêu chí nghiệm thu

- Dán `https://www.qidian.com/book/{id}` thì thấy mục lục đúng tên truyện, quyển và chương, có nhãn VIP.
- Chọn 3 chương miễn phí: text đúng thứ tự, không dính số bình luận, điền vào ô nội dung.
- Chương VIP chưa mua: báo "chỉ có phần xem trước", không tự đưa vào truyện.
- Chương có text bị bảo vệ: báo rõ lý do, không đưa text rác vào truyện.
- Giữa hai chương có nghỉ ít nhất 5 giây. Lần tải thứ hai dùng cache, không gọi lại Qidian.
- Vượt 60.000 ký tự thì chặn và giải thích.
- Hủy giữa chừng thì dừng sau chương hiện tại.

## 8. Quyết định (chốt 04/11/2026)

1. Nguồn: **nhiều trang**. Làm lớp nguồn dùng chung, Qidian trước; Fanqie và Jinjiang làm sau.
2. Vượt 60.000 ký tự: **tự tách** thành nhiều tập của series.
3. Tiếng Trung: **hệ thống tự xử lý** (LLM chia cảnh dịch; dịch trước khi không có LLM).
4. Người dùng: **editor** trở lên.
5. Tài khoản: **tài khoản phụ**.

## 9. Rủi ro

| Rủi ro | Ảnh hưởng | Giảm thiểu |
|---|---|---|
| Qidian chặn IP datacenter hoặc IP ngoài Trung Quốc | Không tải được từ VPS | Kiểm tra ở P0; dùng `VIDTRANS_SOCIAL_BROWSER_PROXY` đã có |
| DOM Qidian thay đổi | Lấy sai hoặc thiếu text | Selector dự phòng (§4); test bằng HTML mẫu; báo lỗi rõ thay vì trả text rỗng |
| Chương VIP luôn bị bảo vệ | Chỉ dùng được chương miễn phí | Chấp nhận; không giải mã (§2) |
| Tài khoản bị khóa | Mất phiên | Tài khoản phụ, giãn cách, chỉ 1 luồng |
| Bản quyền | Pháp lý | Checkbox xác nhận quyền; không lưu text lâu hơn cần thiết ngoài cache |
