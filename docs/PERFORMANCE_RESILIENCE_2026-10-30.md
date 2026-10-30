# Tối ưu tài nguyên và phục hồi mạng — 2026-10-30

Đối chiếu báo cáo `CODE_REVIEW_2026-10-29.md`, tập trung chi phí cache,
hash media, lời gọi provider và kiểm chứng giới hạn tác vụ nền (R21, R24, R30).
Đây không phải bản sửa toàn bộ 30 phát hiện trong báo cáo.

## Thay đổi

- Dùng `sha256_file()` cho kiểm tra cache/checkpoint TTS và speech. Đọc toàn bộ
  nội dung theo luồng với buffer tái sử dụng, tránh nạp cả audio vào RAM.
- Speech tính hash một lần khi cache miss rồi dùng lại key khi ghi kết quả.
  Không đọc được file thì bỏ qua cache, tránh nhận nhầm transcript chỉ vì cùng
  tên file. Đổi namespace sang v3 để không đọc key cũ.
- Cache RAM giới hạn cả số mục (mặc định 256) và tổng payload JSON UTF-8
  (mặc định 32 MiB). Cấu hình bằng `max_memory_entries` và `max_memory_bytes`
  khi tạo `RedisCache`. Giới hạn payload không bao gồm overhead đối tượng Python,
  key, hoặc bộ nhớ tạm khi tuần tự hóa. Dữ liệu vượt giới hạn không được cache.
  LRU, TTL theo đồng hồ monotonic và snapshot JSON ngăn dữ liệu bị sửa ngoài cache.
- Redis có connect/socket timeout 2 giây thay vì đợi mạng không giới hạn.
- Sửa namespace `ChunkedEncodingError` trong helper retry: lỗi timeout/kết nối
  không còn bị che bởi `AttributeError` trong nhánh bắt exception.
- Kiểm tra policy retry: số lần thử nguyên dương, timeout/backoff hữu hạn.
  Dùng tối đa 3 lần thử mặc định, exponential backoff có jitter, tôn trọng
  `Retry-After` trong giới hạn 15 giây. Retry HTTP 408/429/500/502/503/504;
  lỗi chứng chỉ và các HTTP client error còn lại được trả về ngay.
- Hai backend TTS premium dùng helper này, đóng response trong `finally`,
  chuyển lỗi mạng sau khi hết retry thành `SynthesisError` giữ nguyên nguyên nhân.
  Request tạo giọng có thể phát sinh thêm chi phí nếu server đã xử lý nhưng
  response bị mất. Không áp dụng helper cho upload/publish.

## Kiểm chứng

Nhóm test tập trung gồm resource limits, premium TTS resilience, network,
speech/cache, TTS recovery, YouTube collector, mixer và localization coverage.
Test sử dụng fake HTTP/provider. Các test async được chạy ngoài sandbox sau
khi lượt trong sandbox hết hạn 20 giây; harness chặn kết nối TCP/DNS Python.
Kết quả cuối: **64 passed**, 1,61 giây; `LIVE_PROVIDER_CALLS=0`.

Phép đo cô lập với file 64 MiB, một lượt mỗi phương pháp, cùng SHA-256:

| Phương pháp | Bộ nhớ Python đỉnh (tracemalloc) | Thời gian |
| --- | ---: | ---: |
| `read_bytes()` rồi hash | 64,00 MiB | 0,0461 giây |
| `sha256_file()` | 0,376 MiB | 0,0278 giây |

Kết quả chịu ảnh hưởng filesystem cache và thứ tự chạy. Chưa đo throughput,
RSS, CPU/GPU hoặc thời gian xử lý toàn bộ video trên tải production.
