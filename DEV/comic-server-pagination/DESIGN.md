# Thiết kế

## Contract

- `GET /api/v1/comic/projects?page={n}&limit={n}` trả `items,total,page,limit,offset,total_pages`.
- `GET /api/v1/comic/projects/{job_id}?scene_page={n}&scene_limit={n}` chỉ trả lát `storyboard.scenes` và metadata `scenes_pagination`.
- `limit` được validate ở backend; truy vấn dự án dùng SQL `COUNT`, `LIMIT`, `OFFSET`.

## Mapping

- Router: `backend/application/comic_routes.py`.
- Scope/quyền và repository adapter: `backend/main.py::list_comic_projects_for_request`.
- Truy vấn DB: `backend/infrastructure/job_store.py::list_jobs`.
- UI/state: `web/js/comic.js`.

Phân quyền giữ nguyên: server lấy account từ request context; user thường chỉ thấy dữ liệu của mình.

