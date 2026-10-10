# Review kế hoạch

## Findings

| ID | Finding | Mức độ | Xử lý trong plan | Trạng thái |
|---|---|---|---|---|
| RV01 | Hai repo dùng API web private và cookie, không phải TikTok developer API | Critical | Official-first; native adapter sau legal/security/spike gate | OPEN_DECISION |
| RV02 | Retry sau timeout submit có thể tạo bài trùng | Critical | Remote checkpoint, ambiguity state, cấm auto retry hậu side-effect | CLOSED |
| RV03 | Job render và publish đang dùng chung status/payload | High | Publish aggregate/store/state machine riêng | CLOSED |
| RV04 | Lịch local phụ thuộc file còn tồn tại | High | Media pin/refcount + retention test | COVERED |
| RV05 | UI dễ gọi “đã đăng” ngay sau upload | High | Phân biệt submitted/processing/published và reconcile | CLOSED |
| RV06 | Caption có thể lệch dữ liệu đã duyệt | High | Snapshot + adapter contract test giữ newline/hashtag | CLOSED |
| RV07 | Native schedule chưa có status/cancel contract đáng tin cậy | High | Spike go/no-go bắt buộc reconciliation | OPEN_SPIKE |
| RV08 | App chưa audit có thể không đăng public | High | Capability/preflight và `SELF_ONLY` smoke runbook | OPEN_CONFIG |
| RV09 | Nhiều account chưa được support rõ | Medium | Schema chuẩn bị account ID, scope release cần chốt | OPEN_DECISION |

## Kết luận self-review

- Plan đủ để chuyển sang URD/DESIGN sau khi đóng các câu hỏi quyết định.
- Không nên bắt đầu native adapter bằng cách copy endpoint/signing code từ repository tham khảo.
- Critical path production là official direct + durable local schedule + reconciliation; native schedule là nhánh độc lập.
