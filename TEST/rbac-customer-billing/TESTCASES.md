# TESTCASES — RBAC và customer billing

| TC ID | Phạm vi | Kỳ vọng | Trạng thái |
|---|---|---|---|
| TC_001 | JWT admin/user claims | Verify đúng subject/user/role/scope | PASS |
| TC_002 | Account password | Hash được lưu, không nằm trong public response | PASS |
| TC_003 | Approval | Tài khoản chưa approved không nhận session hợp lệ | PASS cấp code/unit; E2E NOT_RUN |
| TC_004 | Admin guard | Editor/viewer gọi `/admin/*` nhận 403 | PASS cấp middleware review; integration NOT_RUN |
| TC_005 | Order ownership | User B không đọc order user A | PASS |
| TC_006 | Server-side amount | Creator = 199.000 VND, không nhận amount client | PASS |
| TC_007 | Invalid plan/provider | Free/Enterprise/unknown/cash bị từ chối | PASS |
| TC_008 | Duplicate payment event | Side effect paid chỉ áp dụng một lần | PASS |
| TC_009 | MoMo request signing | Request HMAC, không lộ secret | PASS |
| TC_010 | MoMo tampered callback | Sai amount sau ký bị từ chối | PASS |
| TC_011 | ZaloPay callback MAC | Đúng nhận, payload bị sửa bị từ chối | PASS |
| TC_012 | Provider missing config | UI disabled, API fail-closed | PASS qua local preview |
| TC_013 | Responsive | 1440/390 không overflow | PASS |
| TC_014 | Merchant sandbox E2E | Redirect + callback thật | BLOCKED — cần merchant credentials và HTTPS callback |
| TC_015 | Bank reconciliation | Admin xác nhận kích hoạt plan | PASS cấp store/API code; UI Admin action NOT_RUN |
| TC_016 | VietQR chuyển khoản MB | QR chứa account server-side, đúng amount và transfer content của order | PASS |
| TC_017 | Bank webhook signature | Raw body đúng HMAC được chấp nhận; payload bị sửa bị từ chối | PASS cấp unit |
| TC_018 | Bank webhook business validation | Chỉ order bank, đúng amount, transaction ID hợp lệ mới được paid | PASS cấp code review; integration NOT_RUN |

