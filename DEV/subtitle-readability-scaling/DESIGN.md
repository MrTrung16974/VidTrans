# Thiết kế — Scale sàn cỡ chữ theo độ phân giải

- URD: `BA_URD/subtitle-readability-scaling/URD.md` v0.1
- Baseline: production job `4037ce00`, 1080×1920, ba cue đều `font_size=22`.

## Thiết kế

`apply_subtitle_layout` là điểm tính layout dùng chung cho ASS, drawtext và Pillow. Tại đây:

1. Tính `resolution_scale = video_height / 720`.
2. Scale và round `font_size`, `min_font_size`, `max_font_size` từ chuẩn 720p.
3. Clamp default/OCR font và truyền sàn đã scale vào `_fit_text`.
4. Không đổi schema `subtitle_layout`, renderer hoặc style API.

Việc scale theo chiều cao phù hợp với logic `font_size` và `margin_v` hiện hữu. Trên baseline, sàn trở thành `round(22 × 1920 / 720) = 59px`. Ở 720p kết quả không đổi.

## Tương thích, bảo mật và rollback

- Style đã lưu tiếp tục hợp lệ; ý nghĩa nhất quán hơn vì toàn bộ kích thước style cùng dùng chuẩn 720p.
- Không DB/API/auth/tenant/log/secret change.
- Rollback bằng revision trước; output cũ không bị ghi đè.

## Test strategy

- Regression toàn bộ `test_subtitle_layout.py`.
- Case mới cho OCR nhỏ ở 1080×1920 phải đạt 59px.
- Case mới xác nhận 720p vẫn giữ sàn 22px.
- Sau deploy, rerender job production và kiểm tra ASS/layout/frame.

