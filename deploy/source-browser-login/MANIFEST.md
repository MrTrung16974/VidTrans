# Manifest

- Version: source-browser-login 1.0
- Không đổi DB, SQL, Camunda/BPMN/DMN.
- Thành phần: backend manager/downloader/API, frontend workspace tabs, Compose sidecars/volumes, Nginx routes.
- Phụ thuộc: image `deploy/douyin-browser`, Playwright backend hiện hữu, RAM đủ cho hai Chromium bổ sung.
- Automated verification: PASS; UAT login thật: NOT_RUN.
