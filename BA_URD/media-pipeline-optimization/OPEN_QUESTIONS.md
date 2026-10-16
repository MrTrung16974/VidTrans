# Open questions — Tối ưu pipeline media

| Q ID | Nguồn/mục | Nội dung thiếu/mâu thuẫn | Ảnh hưởng | Người cần xác nhận | Trạng thái | Quyết định/bằng chứng |
|---|---|---|---|---|---|---|
| Q01 | ANALYSIS/Q01 | Ưu tiên latency một video hay throughput batch? | Cách phân bổ concurrency/resource | Chủ sản phẩm/vận hành | OPEN, không chặn Lát A | Lát A chỉ đo cả hai chỉ số |
| Q02 | ANALYSIS/Q02 | Có cung cấp Fast/Balanced/Quality cho người dùng không? | API/UI và adaptive OCR/ASR | Chủ sản phẩm | OPEN, chặn Lát C UI/policy | Giữ behavior hiện tại trong Lát A/B |
| Q03 | ANALYSIS/Q03 | TTL/scope cross-job cache và hành vi xóa theo tenant? | Privacy/storage/cache hit | Chủ sản phẩm/ATTT | OPEN, chặn cross-job cache | Mặc định per-job |
| Q04 | ANALYSIS/Q04 | Corpus nào được phép dùng và có transcript/bản dịch chuẩn? | Chốt SLO chất lượng | Chủ dữ liệu/QA | OPEN, chặn kết luận NFR_01/02 | Chỉ dùng dữ liệu tổng hợp cho unit test |
| Q05 | ANALYSIS/Q05 | CPU/RAM/GPU/encoder của VPS production? | Concurrency/hardware encoder | Vận hành | OPEN, chặn Lát E production tuning | Không bật hardware encoder |
