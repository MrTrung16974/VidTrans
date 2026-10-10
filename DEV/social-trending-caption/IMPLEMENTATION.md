# Kết quả tối ưu caption và hashtag mạng xã hội

## Thay đổi

- Nâng `LocalExtractiveTikTokProvider` từ `local-extractive-v2` lên `local-extractive-v4`.
- Thêm CTA theo bảy nhóm chủ đề, không thêm tuyên bố nội dung ngoài bản dịch.
- Trộn hashtag theo thứ tự: cụm trực tiếp, nhóm chủ đề chính, tối đa một `#xuhuong`.
- Không thêm `#fyp`, `#viral` hoặc tag khám phá cho nội dung không nhận diện được chủ đề.
- Giữ tối đa 5 hashtag, tôn trọng cấu hình `hashtag_count` và giới hạn phần chữ 350 ký tự.
- Theo ý tưởng từ `tjoab/captionaize`, artifact tách `relevance_hashtags` và `discovery_hashtags`.
- Chủ đề ngoài vocabulary dùng cụm liên tiếp 2–4 từ từ toàn transcript tin cậy; ví dụ kỹ thuật tạo `#ocvit #dungcuchuyendung` mà không thêm `#xuhuong`.

## Nguồn tham khảo

- `https://github.com/topics/captioning-videos`: danh sách dự án video captioning và video understanding.
- `https://github.com/tjoab/captionaize`: ý tưởng tạo nội dung riêng theo nền tảng và tách viral-esque/relevance-esque hashtags.
- Không sao chép code hoặc thêm dependency từ repository tham khảo; VidTrans giữ pipeline local dựa trên transcript hiện có.

## Kiểm chứng

- Ngày chạy: 2026-10-10.
- Lệnh: `PYTHONPATH=backend python3 -m unittest backend.tests.test_tiktok_summary backend.tests.test_tiktok_browser backend.tests.test_tiktok_login_recovery backend.tests.test_tiktok_publisher backend.tests.test_domain_models backend.tests.test_job_scheduler backend.tests.test_job_store`
- Kết quả: `PASS`, 75 test, 0 lỗi.
- Mẫu chủ đề phát triển bản thân tạo hook, CTA và `#baihoccuocsong #phattrienbanthan #xuhuong` trong 129 ký tự.

## Giới hạn

- Đây là chiến lược local theo chủ đề, không đọc bảng xếp hạng hashtag realtime từ TikTok.
- Hiệu quả reach/engagement cần được đo bằng dữ liệu bài đăng thực tế; unit test chỉ xác nhận tính đúng đắn và giới hạn đầu ra.
