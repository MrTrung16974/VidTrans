# Traceability

| Yêu cầu | Design/code | Unit/testcase | Trạng thái |
|---|---|---|---|
| UC_01/AC01_01 | `oauth_store.py`, auth routes | TC_001 | PASS automated |
| UC_02/BR02_01 | Facebook/YouTube adapters | TC_002, TC_003 | PASS automated |
| UC_02/AC02_02 | `reconcile` mỗi adapter | TC_004 | PASS automated |
| UC_02/AC02_01 | `SocialPublishingService`/SQLite store | TC_005 | PASS regression |
| UC_03 | scheduler/store hiện có + UI schedule | generic social tests | PASS regression |
| NFR_01/02 | token store/state TTL/public response allowlist | TC_001 + review | PASS automated/review |
| Staging publish | External Meta/Google | TC_008, TC_009 | NOT_RUN |

