# Thiết kế tối ưu caption và hashtag mạng xã hội

## Mục tiêu

- Caption ngắn, có hook từ nội dung thật và một CTA tự nhiên để tăng khả năng tương tác.
- Hashtag gồm chủ đề cụ thể và tối đa một tag khám phá; không nhồi `#fyp`, `#viral` hoặc tag không liên quan.
- Giữ bộ tạo local, xác định và không cần API/network.

## Hiện trạng

`LocalExtractiveTikTokProvider` v2 chọn tối đa hai câu dịch và hashtag khi cụm từ xuất hiện chính xác. Kết quả trung thực nhưng thiếu CTA, bỏ sót các biến thể chủ đề và không có lớp hashtag khám phá.

## Thiết kế v3

- Giữ nguyên cơ chế loại câu trùng, ưu tiên bản dịch tin cậy và chọn hook.
- Nhận diện nhóm chủ đề bằng cụm từ/biến thể đã định nghĩa: đời sống/phát triển bản thân, ẩm thực, du lịch, làm đẹp, thể thao, gia đình/tình cảm, học tập/công việc.
- Sinh CTA theo nhóm chủ đề, chỉ đặt câu hỏi tương tác và không thêm tuyên bố sự thật mới.
- Hashtag ưu tiên tag cụ thể xuất hiện trực tiếp, sau đó tag nhóm chủ đề, cuối cùng tối đa một `#xuhuong` nếu đã nhận diện được chủ đề và còn quota.
- Tổng hashtag tối đa 5 và tôn trọng `hashtag_count`; nội dung không nhận diện được chủ đề không nhận tag chung.
- Caption tối đa `min(max_summary_chars, 350)` cho phần chữ, tối đa ba đoạn: nội dung, CTA, hashtag.

## Mở rộng v4 từ tham khảo GitHub

Nguồn tham khảo: topic `captioning-videos`, đặc biệt `tjoab/captionaize`; các dự án video understanding/grounding trong cùng topic được dùng ở mức ý tưởng, không sao chép model hoặc code.

- Tách metadata hashtag thành `relevance_hashtags` và `discovery_hashtags`, tương tự cách Captionaize phân biệt hashtag bám nội dung với hashtag có tính lan truyền.
- Với chủ đề chưa có trong vocabulary, dùng toàn bộ câu dịch tin cậy để xếp hạng cụm liên tiếp 2–4 token; không cắt cửa sổ bigram gây hashtag cụt.
- Hashtag động được xếp sau tag chủ đề đã kiểm chứng; tag discovery vẫn chỉ có `#xuhuong` và chỉ xuất hiện khi nhận diện được nhóm chủ đề.
- Không tích hợp Gemini File API của Captionaize: VidTrans đã có ASR/OCR/transcript, tránh upload video thêm lần nữa, tránh phụ thuộc cloud và chi phí mới.

## Contract và tương thích

- Không đổi API đầu vào; artifact bổ sung hai field tùy chọn và đổi `generator` thành `local-extractive-v4`.
- File JSON cũ vẫn đọc được vì các field giữ nguyên.
- Không thay đổi auth, dữ liệu người dùng, DB hoặc side effect bên ngoài.

## Kiểm thử

- Tính xác định, giới hạn độ dài và số hashtag.
- CTA đúng nhóm chủ đề và không lặp trong caption.
- Hashtag chủ đề đứng trước `#xuhuong`; không có `#fyp`/`#viral`.
- Nội dung lạ không nhận hashtag khám phá; nội dung `needs_review` không ảnh hưởng caption/tag.
- Regression ghi artifact và API gợi ý caption.
