# Traceability — Subtitle readability scaling

| Yêu cầu | Design/code | Unit test | TC | Deploy | Trạng thái |
|---|---|---|---|---|---|
| BR01_01/AC01_01 | `subtitle_layout.py::apply_subtitle_layout` | `test_scales_readability_floor_for_vertical_high_resolution_video` | TC_001 | Production image 2026-11-05 | PASS |
| BR01_04/AC01_02 | Cùng hàm | `test_keeps_existing_readability_floor_at_720p` | TC_002 | N/A | PASS |
| BR01_03/AC01_03 | `_fit_text` với scaled floor | `test_long_cue_shrinks_and_never_runs_past_frame` | TC_003 | N/A | PASS |
| AC01_04 | Preview job `4037ce00` | N/A | TC_004 | VPS `169.58.116.223` | PASS preview; output cũ giữ nguyên |

