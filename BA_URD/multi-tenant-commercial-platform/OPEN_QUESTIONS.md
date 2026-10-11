# OPEN QUESTIONS — VidTrans multi-tenant và thương mại hóa

| Q ID | Nguồn/mục | Nội dung thiếu/mâu thuẫn | Ảnh hưởng | Người cần xác nhận | Trạng thái | Quyết định/bằng chứng |
|---|---|---|---|---|---|---|
| Q-01 | Phạm vi MVP | MVP chỉ có tài khoản cá nhân hay cần workspace/agency nhiều thành viên ngay? | Schema quyền, UI, onboarding và thời gian ra mắt | Product owner | RESOLVED | Người dùng duyệt tài khoản cá nhân; vẫn dùng tenant/membership, 2026-10-10 |
| Q-02 | Metering | Phút tính theo video nguồn, video đầu ra hay thời gian compute? Retry tính thế nào? | Billing ledger, quota và khiếu nại | Product owner | OPEN | Đề xuất video nguồn/job thành công; retry lỗi hệ thống miễn phí |
| Q-03 | Pricing | Mỗi gói có bao nhiêu phút, giá bao nhiêu và credit hết hạn khi nào? | Pricing page, subscription và gross margin | Product owner | OPEN | Cần benchmark cost/phút trước khi chốt |
| Q-04 | Platform | YouTube/Facebook thuộc MVP hay beta sau? | Timeline, legal review và test matrix | Product owner | OPEN | Đề xuất beta sau |
| Q-05 | Payment | Chọn cổng thanh toán Việt Nam nào? | API/webhook, đối soát, hoàn tiền | Product/Finance | OPEN | So sánh sau khi có pháp nhân và volume dự kiến |
| Q-06 | Infrastructure | Mức tải và ngân sách pilot là bao nhiêu? | Sizing worker, GPU/CPU, storage và SLA | Product/Tech | DEFERRED | Người dùng xác nhận tính sau ngày 2026-10-10 |
| Q-07 | Admin access | Full quyền xem/tải nội dung cần kiểm soát vận hành nào? | Riêng tư, audit, trách nhiệm nội bộ | Product/Legal | OPEN | Quyền đã CONFIRMED; đề xuất bắt buộc reason + audit + rate limit |
