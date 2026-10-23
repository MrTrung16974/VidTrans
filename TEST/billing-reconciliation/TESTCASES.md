# Test cases

| ID | AC | Mục tiêu | Trạng thái |
|---|---|---|---|
| TC_001 | AC01 | Khách báo chuyển khoản → `pending`, plan `free`, báo lặp giữ timestamp | PASS |
| TC_002 | AC02 | User khác báo/hủy đơn → 422, đơn không đổi | PASS |
| TC_003 | — | Đơn MoMo không báo thủ công được | PASS |
| TC_004 | AC03, AC04 | Admin list (đơn đã báo đứng đầu, có email), lọc chờ đối soát, xác nhận → `paid`, plan `pro`, audit | PASS |
| TC_005 | AC05 | Admin từ chối → `cancelled`, audit có lý do | PASS |
| TC_006 | AC06 | Non-admin gọi list/confirm/reject → 403 | PASS |
| TC_007 | — | UI không còn hứa "Kích hoạt gói", có tab admin | PASS |
| TC_008 | — | UAT trình duyệt 1440/390 | NOT_RUN |
