# Manifest

- Version: source-browser-login 1.0
- Không đổi DB, SQL, Camunda/BPMN/DMN.
- Thành phần: backend manager/downloader/API, frontend workspace tabs, Compose `social-browser`, shared volume và Nginx routes.
- Phụ thuộc: image `deploy/douyin-browser`, Playwright backend hiện hữu, RAM đủ cho một Chromium nguồn và một Chromium TikTok đăng bài.
- Automated verification: PASS; UAT login thật: NOT_RUN.
