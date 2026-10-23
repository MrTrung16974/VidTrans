# Kế hoạch

- Feature: `logout-login-default`
- Trạng thái: `IN_REVIEW`
- Phạm vi triển khai được yêu cầu trực tiếp.

| Bước | Việc làm | Kiểm chứng |
|---|---|---|
| DEV | Reset mode và form trong `lockApplication` | Static test + syntax |
| TEST | Bao phủ logout/session expiry dùng chung helper | Unit/static regression |

