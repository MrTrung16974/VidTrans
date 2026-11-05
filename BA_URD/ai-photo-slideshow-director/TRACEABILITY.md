# Traceability

| UC/AC | Design/code | Unit/check | TC | Trạng thái |
|---|---|---|---|---|
| UC_01/AC01–03 | `photo_slideshow_planning.py`, endpoint `ai-plan` | planner unit PASS; API integration NOT_RUN | TC_001–TC_005 | PARTIAL |
| UC_02/AC04 | web photo AI card; Flutter photo card/repository | JS syntax PASS; Flutter analyze PASS | TC_006–TC_009 | PARTIAL |
| UC_03/AC05 | plan submit validation + `render_planned_slideshow` | command unit PASS; real FFmpeg NOT_RUN | TC_010–TC_013 | PARTIAL |
| AC06 | manual `render_timed_slideshow` branch unchanged | existing timed tests PASS | TC_014 | PASS (unit) |

