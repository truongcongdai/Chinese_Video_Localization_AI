# Sửa lỗi và tối ưu theo review ngày 30-10-2026

Áp dụng cho working tree hiện tại, giữ các thay đổi đã có trước lượt sửa.
Các mã F bên dưới tham chiếu `CODE_REVIEW_2026-10-30.md`; báo cáo review cũ được
giữ làm bằng chứng tại thời điểm rà soát, không đại diện trạng thái sau sửa.

## Thay đổi theo nhóm phát hiện

| Phát hiện | Thay đổi |
| --- | --- |
| F01–F04 | Docker chỉ COPY nguồn cần thiết, loại secret/runtime bằng .dockerignore; Windows dùng template cấu hình; DB bot chuyển vào /app/data; app/web dùng đúng host port Redis 6380. |
| F05–F09 | Redis claim/cancel/requeue nguyên tử bằng Lua; claim token, lease, heartbeat và reclaim; worker cũ không được ghi đè owner mới; enqueue không chuyển sang RAM sau lỗi Redis chưa rõ kết quả. Queue RAM ưu tiên URGENT và FIFO; sửa unfinished-task accounting; JobService truyền lỗi cho queue retry và xử lý enqueue bị từ chối. |
| F10–F11 | Transaction số dư có điều kiện, audit cùng transaction; shared ClosingConnection đóng SQLite ngay khi thoát transaction cho cache, Store và Content OS repository. |
| F12 | Chuẩn hóa UTC; tính maxima engagement từ tập ứng viên trước chấm điểm. |
| F13–F15 | Giữ đúng Facebook Page/token snapshot; lease bền vững tuần tự hóa run publishing; Facebook có trạng thái outcome_unknown và chặn upload mới khi kết quả cũ chưa đối soát. |
| F16–F21 | Ledger ngân sách lưu trước gọi provider, giữ qua failure/restart; kiểm tra từng leaf operation và retry; lease/fencing cho advance/retry; giữ cancel/pause; chỉ nhận output publish hoàn thành đúng artifact/destination. |
| F22–F24 | Admission cycle trong write transaction; tự sửa linkage chưa hoàn tất theo idempotency key; tạo occurrence/cycle bền vững trước dời lịch. |
| F25–F26 | Chuẩn hóa hậu tố @Bot trong command; await gửi video và theo dõi lỗi task delivery. |
| F27–F28, F34–F36 | Publishing assets chỉ đọc pack riêng của owned job; path rỗng không trỏ cwd; escape đúng HTML/attribute context, dùng text/DOM properties; sửa row actions Content OS. |
| F29–F33, F37 | Identity state gắn cookie trình duyệt; tôn trọng đóng đăng ký; chỉ tin email verified, bỏ tự liên kết password account theo email; sanitize lỗi provider. Social QR đi qua handoff đăng nhập đúng app owner trước consent; callback kiểm tra cùng owner trước exchange; thêm xác nhận account trước kích hoạt credential. |
| F38–F40 | Claim top-up trong transaction; giữ chỗ credit lúc create/retry/rerun; refund theo reservation ID; job/post có owner lease và chỉ recover owner hết hạn, có grace period cho legacy work. |
| F41–F45 | Giữ trạng thái chờ approval; lưu revised SCRIPT version; approval gắn version/checksum; storyboard manual chờ duyệt và resume bản đã lưu; valid=False chặn completion; đồng bộ terminal state job/run. |
| F46–F48 | Chọn reup actionable/due và xoay vòng batch; chuẩn hóa duration từng platform, chặn tự xử lý nguồn chưa biết thời lượng; explicit new attempt cho nguồn cancelled an toàn. |
| F49–F54 | WAV fast path xác minh RIFF/data length, fallback FFprobe cho header unknown; bỏ await khi giữ thread lock; CPU Whisper không tự lấy lại lock; cleanup box không loại caption; slot nặng và throttle chỉ release khi work thực sự kết thúc, kể cả cancel. |
| F55–F58 | Demucs cache theo hash nguồn/options với completion manifest và lock liên tiến trình; giữ phần dub dài trong mixer, localization từ chối overflow để chỉnh script thay vì cắt lời; music analysis offload, excerpt 30 giây và cache features có giới hạn; yt-dlp trả filepath/container thực tế. |
| F59–F63 | Dựng lại FFmpeg split-screen graph/maps; đồng bộ atempo với video speed; TTS báo lỗi input quá dài thay vì cắt nội dung; đóng AsyncClient trong finally theo scope translation/batch; voice matching trả voice ID đầy đủ. |

## Phục hồi mạng và tài nguyên

- Provider POST có tối đa ba lần thử mặc định, timeout connect/read hữu hạn,
  exponential backoff có jitter và Retry-After có trần. Lỗi chứng chỉ và HTTP
  client error không transient không được retry tự động.
- Từng retry transport tiêu ngân sách automation trước request tiếp theo.
  Các lỗi xác định như script quá dài hoặc dub vượt video không chạy lại toàn bộ
  pipeline; user cần chỉnh nội dung trước thực thi lại.
- Không retry upload Facebook khi response mất và chưa có external ID. Cần
  đối soát kết quả từ xa; không có tuyên bố exactly-once cho hiệu ứng ngoài hệ thống.
- Thread đăng lịch và Content OS giữ heartbeat đến khi công việc thực sự trả về.
  Polling worker tiếp tục phục hồi lease hết hạn sau startup, claim một bài theo
  sức chứa thực thi, và tiếp tục polling sau lỗi SQLite/mạng thay vì chết ngầm.
- Full-content hash đọc theo luồng; cache RAM mặc định tối đa 256 mục và 32 MiB
  payload JSON. Giới hạn không bao gồm toàn bộ overhead/RSS của tiến trình.
- FFmpeg audio filter threads mặc định hai; concurrency stage vẫn cấu hình bằng
  các biến TRANSCRIPTION/OCR/RENDER/TTS/AUDIO_MIX_CONCURRENCY của ứng dụng.

## Đo cô lập và kiểm chứng

Đo trên file 64 MiB, một lượt mỗi phương pháp, cùng SHA-256:

| Cách hash | Bộ nhớ Python đỉnh (tracemalloc) | Thời gian |
| --- | ---: | ---: |
| read_bytes rồi hash | 64,000 MiB | 0,0563 giây |
| hash theo luồng | 0,377 MiB | 0,0347 giây |

Với cache SQLite mới, 100 set + 100 get khi hoãn GC giữ số file descriptor ở
**4 → 4**, thay vì tích tụ đến khi collection. Đây là phép đo cục bộ trên Linux;
chưa đo throughput/RSS/CPU toàn pipeline hoặc tải GPU thật.

Các test mới kiểm tra race hai connection, rollback khi audit lỗi, Redis thật
qua Unix socket, lease expiry/heartbeat/stale completion, cancel, budget qua
failure/retry, upload timeout, OAuth owner, giới hạn đường dẫn, approval/artifact
version và FFmpeg với audio/video cục bộ. Fixture cũ được cập nhật để tạo approval
và audio hợp lệ, hoặc supply duration khi kiểm tra automation; không bỏ các gate mới.

Kết quả trên working tree sau sửa:

- **1.322 passed, 9 skipped**, 157,36 giây trong lượt pytest toàn bộ cuối cùng.
  Sáu test Content OS API bị tắt bởi feature flag, một renderer test được đánh
  dấu skip cho CI, hai test thiếu provider tùy chọn DeepL/Azure. Có 269 warning,
  chủ yếu deprecation của các API datetime/FastAPI/TestClient hiện có.
- **6 file test JavaScript đạt** bằng `node --test tests/*.js`.
- Parse AST **253 file Python** và `node --check` **12 file JavaScript** đạt;
  `git diff --check` đạt.
- Harness `/tmp/uvai_review_offline.py` vô hiệu dotenv, chuyển runtime path về
  thư mục tạm, chặn TCP/DNS Python; Redis integration dùng Unix socket cục bộ
  với tiến trình thử nghiệm được đóng ở teardown. `LIVE_PROVIDER_CALLS=0`.
- Log cuối: `/tmp/uvai-final-pytest-verified.log`, `/tmp/uvai-final-node.log`;
  phép đo cô lập: `/tmp/uvai-final-benchmark.json`.
- Bộ chạy tái lập đã được lưu tại `scripts/run_tests.py`; chạy
  `python scripts/run_tests.py -q` và `node --test tests/*.js` từ thư mục gốc.
  CI dùng cùng bộ chạy offline. Smoke test `scripts/run_web.py` với database
  tạm đã trả HTTP 200 cho `/health`, `/` và `/openapi.json`.
- Gate trước push bằng chính `scripts/run_tests.py`: **1.322 passed, 9 skipped**,
  158,67 giây; `LIVE_PROVIDER_CALLS=0`. Sáu file test JavaScript đạt.

## Lưu ý khi áp dụng

1. **Chưa deploy hoặc thay dữ liệu runtime.** Trước recreate container bot hiện
   hữu, sao lưu/copy `/app/database.sqlite3` cũ ra ngoài container, kiểm tra
   integrity, rồi đưa bản sao vào volume `db-data` ở `/app/data/database.sqlite3`.
   Đổi đường dẫn Compose không tự chuyển dữ liệu writable layer của container cũ.
2. Approval legacy không có binding version/checksum phải được duyệt lại trước
   production. Demucs basename cache cũ không được tái dùng vì không xác minh nguồn.
3. QR trên thiết bị khác yêu cầu đăng nhập cùng app owner trước cấp quyền provider.
   Không tự merge tài khoản mật khẩu và SSO chỉ vì trùng email.
4. Lease và semaphore là các cơ chế khác nhau: Redis/SQLite lease phối hợp owner;
   semaphore tài nguyên hiện tại giới hạn trong từng process. Cần đặt số process
   phù hợp tài nguyên CPU/GPU thực khi vận hành.
5. Kiểm thử dùng Python 3.14.4 hiện có. Chưa chạy build Windows/Docker, API provider
   thật, hay kiểm thử tương đương trên Python 3.11/3.12 của CI.
