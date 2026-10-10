# Traceability

| Requirement | Design/code | Unit/API test | TC | Trạng thái |
|---|---|---|---|---|
| BR01/AC01 | publishing service, publisher callback, routes/UI | `test_tiktok_publishing`, `test_tiktok_publisher` | TC_001–004 | PASS |
| BR02/AC02 | publish store + scheduler | `test_due_schedule...` | TC_005–007 | PASS |
| BR03/AC03 | publisher draft + service mode | publisher/service tests | TC_008 | PASS |
| BR04/AC04 | checkpoint/reconciliation/public DTO | ambiguity + route tests | TC_009–010 | PASS |
| NFR_01–05 | SQLite transaction, scheduler lifecycle, safe DTO | regression/syntax tests | TC_010–013 | PASS_WITH_KNOWN_SUITE_BLOCKER |
