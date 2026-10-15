# Traceability

| Yêu cầu | Design/code | Unit/static test | Testcase | Deploy |
|---|---|---|---|---|
| AC01 | `index.html`, `app.js`, `browser-frame.js` | `test_frontend_static`, `test_browser_frame.mjs` | TC_001/006 | RUNBOOK |
| AC02/BR01 | `social_browser_auth.py` | `test_social_browser_auth.py` | TC_002–004 | MANIFEST |
| AC03/BR02 | `social_video_downloader.py`, `main.py` | `test_social_video_downloader.py` | TC_005/009 | RUNBOOK |
| AC04 | manager logout + API | unit design review | TC_010 NOT_RUN | RUNBOOK |
| AC05/NFR01 | domain filter, Nginx auth_request | URL/domain tests + compose | TC_002/006 | MANIFEST |
| AC06 | giữ Douyin manager/routes/hash | Douyin/browser tests | TC_007 | rollback notes |
