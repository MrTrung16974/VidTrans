# Traceability — ComfyUI integration

| UC/BR/AC/NFR | Nguồn | URD | Design/code | Unit test | TC | Deploy artifact/gap | Trạng thái |
|---|---|---|---|---|---|---|---|
| UC_01/BR01_01/AC01_01 | SRC_02-04 | v0.1 | `comic_routes.py`, `image_provider_statuses` | API test hiện hữu | TC_001, TC_011 | RUNBOOK | PARTIAL: API regression NOT_RUN |
| UC_01/AC01_02 | SRC_02-04 | v0.1 | `ComfyUIImageProvider.__post_init__`, route status check | `test_requires_url_and_checkpoint` | TC_002 | RUNBOOK | PASS unit |
| UC_02/BR02_01-04 | SRC_02 | v0.1 | `ComfyUIImageProvider` | image provider suite | TC_003-TC_008 | RUNBOOK | PASS unit |
| UC_02/AC02_01 | SRC_02 | v0.1 | production adapter | mock happy path + real provider smoke | TC_005, TC_010 | SDXL Base 1.0 local | PASS real smoke |
| UC_02/AC02_02 | SRC_05 | v0.1 | `ComicVideoProducer` fallback | existing comic video tests | TC_006, TC_011 | RUNBOOK | PARTIAL |
| NFR_01-04 | SRC_02/SRC_06 | v0.1 | compose/provider/review | review + unit | TC_006-TC_008 | RUNBOOK | PASS review/unit |
| NFR_05 | runtime smoke | v0.1 | local ComfyUI | `/system_stats` | TC_009 | RUNBOOK | PASS readiness |

