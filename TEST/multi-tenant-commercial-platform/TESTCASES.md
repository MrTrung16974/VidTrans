# TESTCASES — n8n workflow events

- Feature: `multi-tenant-commercial-platform/n8n-events`
- Dữ liệu: synthetic, không PII/secret.
- Phạm vi: outbox và webhook contract; integration n8n thật chưa chạy.

| TC ID | Yêu cầu | Nhóm | Tiền điều kiện | Bước | Kết quả mong đợi | Trạng thái |
|---|---|---|---|---|---|---|
| TC_001 | Payload tối thiểu | Security/P0 | Event sink | Emit event với path/URL/cookie/filename | Payload chỉ có allow-list | PASS |
| TC_002 | Không spam progress | Functional/P0 | Job translating | Chỉ đổi progress | Không tạo event | PASS |
| TC_003 | Mốc dịch | Functional/P0 | Job translating | Chuyển `translation-ready` | Một `translation.completed` | PASS |
| TC_004 | Phân biệt nguồn | Functional/P1 | Upload/social job | Chuyển `source-ready` | `source.ready` hoặc `download.completed` đúng loại | PASS |
| TC_005 | Durable outbox | Persistence/P0 | SQLite tạm | Enqueue → claim → delivered | State đúng và payload giữ nguyên | PASS |
| TC_006 | Retry/dead | Failure/P0 | Event pending | Đánh dấu lỗi đến max attempts | Pending rồi dead | PASS |
| TC_007 | HMAC | Security/P0 | Secret synthetic | Gửi request mock | Signature khớp exact raw body | PASS |
| TC_008 | Transport | Security/P0 | Remote HTTP URL | Validate config | Từ chối, yêu cầu HTTPS | PASS |
| TC_009 | n8n duplicate | Integration/P0 | n8n test env | Gửi cùng event ID hai lần | Side effect chỉ một lần | NOT_RUN |
| TC_010 | n8n outage | Resilience/P0 | Dispatcher + fake client | Dependency lỗi | Media state độc lập; event về pending để retry | PASS ở code level; actual n8n NOT_RUN |
| TC_011 | Admin list privacy | Security/P0 | Event tồn tại | Gọi list API/Admin page | Không trả payload/video/path/URL/secret | PASS |
| TC_012 | Replay dead event | Functional/P0 | Event `dead`, n8n enabled | Admin bấm Gửi lại | Reset attempts/error, chuyển pending, đánh thức dispatcher | PASS cấp store; UI/API live với enabled chưa chạy |
| TC_013 | Replay non-dead | Negative/P1 | Event pending/delivered | Gọi replay | Từ chối, không đổi state | PASS |
| TC_014 | Admin UI disabled | UI/P1 | n8n chưa cấu hình | Mở `/#admin` | Hiển thị cảnh báo, metric 0, không console error | PASS |
| TC_015 | Dispatcher delivery | Integration/P1 | Event pending + fake client | Start dispatcher | POST contract được giao và event thành delivered | PASS |
| TC_016 | n8n dev compose | Deployment/P1 | Secret synthetic | Render Compose config | Config hợp lệ, Postgres health dependency và localhost binding | PASS |
