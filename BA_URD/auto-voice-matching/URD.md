# URD — Tối ưu khớp giọng tự động

## UC_01 — Chọn kiểu giọng TTS tự động

- Actor: người dùng tạo video ở chế độ có lồng tiếng.
- Tiền điều kiện: có audio nguồn PCM và các cue đã nhận diện.
- Luồng chính: hệ thống phân tích pitch từng cue, tổng hợp bằng chứng toàn video, ổn định chuỗi quyết định và gán `male`/`female` cho TTS.
- Ngoại lệ: cue không có tiếng nói dùng cue lân cận, giọng chủ đạo hoặc fallback cấu hình.

## Quy tắc

- `BR01_01`: quyết định có confidence cao được giữ nguyên.
- `BR01_02`: cue không rõ nằm giữa hai cue cùng loại kế thừa loại đó.
- `BR01_03`: một điểm đổi giọng đơn lẻ chỉ bị làm mượt khi confidence thấp.
- `BR01_04`: không đủ bằng chứng toàn video thì dùng fallback người dùng.
- `BR01_05`: metadata phải chỉ ra raw analysis và nguồn quyết định cuối.
- `BR01_06`: TTS được phép tận dụng một phần khoảng nghỉ trước cue tiếp theo để giảm tăng tốc không cần thiết.
- `BR01_07`: tempo tự động phải có giới hạn tự nhiên và ghi nhận phần thời lượng còn vượt.
- `BR01_08`: người dùng chọn được sắc thái Tự nhiên, Ấm áp, Tươi sáng, Trầm hoặc Năng động.
- `BR01_09`: preset phải được backend kiểm tra theo allowlist; giá trị lạ bị từ chối.
- `BR01_10`: preset hoạt động cùng cả chế độ tự động lẫn thủ công và được lưu để resume job.

## Acceptance criteria

- `AC01_01`: giữ đúng phân loại tín hiệu thấp/cao rõ ràng.
- `AC01_02`: giảm đổi giọng giả trên chuỗi cue cùng người nói.
- `AC01_03`: giữ chuyển giọng khi có hai cue liên tiếp hoặc confidence mạnh.
- `AC01_04`: không phá contract đầu vào/đầu ra hiện tại.
- `AC01_05`: câu có khoảng nghỉ sau nó dùng cửa sổ dài hơn nhưng vẫn chừa khoảng chuyển tiếp.
- `AC01_06`: câu quá dài không bị tăng tốc vượt giới hạn cấu hình nội bộ.
- `AC01_07`: lựa chọn preset được truyền xuyên UI → request → job → TTS metadata.
