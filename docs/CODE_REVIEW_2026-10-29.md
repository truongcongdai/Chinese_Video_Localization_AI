# Rà soát mã nguồn — 2026-10-29

Đánh giá: **REQUEST CHANGES** trước khi vận hành tự động lâu dài.

## Phạm vi và phương pháp

- Rà soát trạng thái working tree hiện tại, gồm thay đổi chưa commit và file mới; không giới hạn ở diff.
- Đã đọc cấu hình `/home/dai/.codex/agents/code-reviewer.toml`. Công cụ không đăng ký agent type `code-reviewer`, nên dùng các agent con mặc định được giao nguyên cấu hình và vai trò này. Chia phạm vi: web/bảo mật; automation/publishing; media pipeline; Content OS/analytics; agent chính kiểm tra jobs, bot, database và triển khai.
- Đối chiếu `PROJECT_BRAIN/prompts/reviewer.md`, checklist và tài liệu kiến trúc. Báo cáo ưu tiên lỗi hành vi và rủi ro vận hành, không liệt kê sở thích định dạng.
- Không sửa mã ứng dụng. Kiểm tra tái hiện dùng Redis giả, HTTP giả và SQLite tạm. Không đọc nội dung `.env` hoặc token.
- Đây là rà soát tĩnh kết hợp kiểm tra cô lập; không chứng nhận đã kiểm tra mọi nhánh thực thi, tải GPU thật hoặc hợp đồng API mạng xã hội hiện hành.

Mức độ: **P1** cần xử lý trước khi triển khai hoặc bật luồng tự động liên quan; **P2** cần sửa để đảm bảo tính đúng đắn/khả năng phục hồi. Mỗi phát hiện ghi rõ điều kiện xảy ra; không có nghĩa mọi cấu hình đều chịu cùng tác động.

## Các phát hiện đã xác minh

### R01 — P1: Docker đưa secret và dữ liệu runtime vào image

Vị trí: `Dockerfile:18` (`COPY . .`); không có `.dockerignore` trong thư mục build.

Build từ workspace hiện tại sẽ đưa `.env`, dữ liệu runtime và virtualenv vào build context/image. Đã xác nhận `.env` tồn tại mà không đọc nội dung. Dung lượng tại thời điểm kiểm tra: `local_data` khoảng **185 GB**, `.venv` khoảng **8,2 GB**; còn các thư mục khác. Điều này vừa có thể làm lộ credential cho người có quyền đọc image/cache/registry, vừa gây build chậm, tốn đĩa hoặc hết dung lượng.

Đề xuất: bổ sung `.dockerignore` loại secret, `.git`, virtualenv, database, cookie, cache/model/output runtime; ưu tiên `COPY` có chọn lọc các thành phần cần chạy. Không dựa vào `.gitignore` để lọc Docker context.

### R02 — P1: Database bot nằm ngoài volume bền vững

Vị trí: `docker-compose.prod.yml:7`, `:11`, `:20`.

Bot được chạy với `--db /app/database.sqlite3` nhưng named volume gắn vào `/app/data`. Vì thế database thực tế nằm trong writable layer của container. Tạo lại container khi deploy có thể mất số dư, audit và lịch sử. `restart` cùng container khác với recreate: phát hiện này áp dụng cho recreate/thay container.

Đề xuất: thống nhất đường dẫn thành `/app/data/database.sqlite3`, chuyển dữ liệu cũ bằng quy trình backup/migration, rồi xác minh recreate giữ dữ liệu.

### R03 — P1: Hai Redis worker có thể cùng nhận một job

Vị trí: `src/universal_video_ai/jobs/queue.py:240–253`.

`ZRANGE` và `ZREM` là hai thao tác riêng; code bỏ qua kết quả `ZREM`. Hai consumer cùng đọc phần tử đầu có thể đều trả về cùng envelope dù chỉ một consumer xóa thành công. Tác vụ render, trừ tiền hoặc publish có thể chạy hai lần.

Bằng chứng: Redis giả dùng barrier để ép hai consumer đọc cùng phần tử; cả hai lần dequeue đều trả `one-job`.

Đề xuất: claim nguyên tử bằng Lua/transaction hoặc queue có cơ chế pending/ack. Chỉ worker claim thành công được thực thi; tác vụ tạo hiệu ứng bên ngoài cũng cần idempotency.

### R04 — P1: Job Redis bị bỏ lại khi worker chết giữa chừng

Vị trí: `src/universal_video_ai/jobs/queue.py:246–253`; `src/universal_video_ai/jobs/worker.py:79–83`.

Dequeue xóa job khỏi pending trước khi callback hoàn thành. Không có in-flight lease, ack, heartbeat hoặc reclaim cho worker chết. Sau crash, hash vẫn `running` nhưng job không còn trong pending; khởi động worker mới không tự lấy lại job này.

Bằng chứng: sau dequeue và giả lập mất consumer, dequeue kế tiếp trả `None`, trạng thái lưu vẫn `running`.

Đề xuất: thêm in-flight lease và reclaim; cân nhắc Redis Streams consumer groups. Retry khi callback ném exception không xử lý được trường hợp tiến trình chết.

### R05 — P2: Hủy job đang chạy có thể bị retry ghi đè

Vị trí: `src/universal_video_ai/jobs/worker.py:84–96`; `src/universal_video_ai/jobs/queue.py:215`.

Nếu job được hủy lúc callback đang chạy rồi callback ném lỗi, nhánh exception vẫn gọi `requeue`. `requeue` đổi trạng thái từ `cancelled` thành `pending`; lần thực thi sau không còn biết job từng bị hủy.

Bằng chứng: callback giả gọi `cancel()` rồi ném `OSError`; kết quả là `pending` và job xuất hiện lại trong pending set.

Đề xuất: kiểm tra trạng thái hủy trước retry và dùng cập nhật trạng thái có điều kiện/nguyên tử để tránh race giữa cancel và requeue.

### R06 — P2: Retry của JobService không đến được JobQueue

Vị trí: `src/universal_video_ai/jobs/service.py:122–136`.

Khi `use_queue=True`, wrapper bắt exception của callback, cập nhật `FAILED` rồi trả về bình thường. `JobQueue._worker` coi callback thành công nên không chạy retry, dù mặc định `max_retries=3`. Một lỗi tải/provider tạm thời trở thành thất bại vĩnh viễn ở lớp service.

Bằng chứng: callback luôn ném `OSError` chỉ được gọi **1 lần**, trạng thái `failed`, cấu hình retry của queue là **3**.

Đề xuất: quy định một lớp sở hữu retry. Nếu queue sở hữu, truyền exception lên và chỉ đánh dấu thất bại cuối cùng sau khi hết retry; đồng thời xử lý trường hợp enqueue trả `False`.

### R07 — P2: Hàng đợi trong RAM xử lý LOW trước URGENT

Vị trí: `src/universal_video_ai/jobs/queue.py:375–381` và `QueuedJob` tại `:291`.

`PriorityQueue` lấy số nhỏ nhất, nhưng enum gán LOW=1, URGENT=4 và enqueue dùng nguyên `priority.value`. Khi có backlog, tác vụ thường/ít ưu tiên chạy trước tác vụ khẩn. Các test hiện có còn xác nhận thứ tự số tăng dần nên không phát hiện sai ngữ nghĩa.

Bằng chứng: enqueue urgent và low trước khi có consumer; phần tử đầu lấy ra là `low`.

Đề xuất: đảo dấu/thứ tự priority và dùng timestamp/counter để FIFO giữa các job cùng priority; test thứ tự thực thi theo yêu cầu nghiệp vụ.

### R08 — P2: Credit có thể bị chi vượt số dư khi dùng nhiều kết nối

Vị trí: `src/universal_video_ai/database/manager.py:615–632`; mẫu tương tự tại `:635–646`.

`RLock` chỉ bảo vệ một instance. Hai `DatabaseManager` cùng database có thể cùng đọc số dư cũ, cùng vượt qua kiểm tra rồi ghi đè `new_balance`. Đây là lỗi khi nhiều instance/tiến trình dùng chung DB legacy; không khẳng định một bot đơn luồng hiện tại tự tạo race này.

Bằng chứng: số dư đầu **3**, hai kết nối đồng thời trừ **2** đều trả `True`; cuối cùng số dư **1**, `total_used=4`.

Đề xuất: cập nhật số dư tương đối với điều kiện `credits >= amount` trong transaction; kiểm tra số hàng cập nhật; audit nằm trong cùng transaction. Áp dụng cho cộng/trừ/điều chỉnh liên quan.

### R09 — P1: Stored XSS chạy trong phiên quản trị

Vị trí: `src/universal_video_ai/web/static/app.js:6410–6414`; trường username cũng bị nội suy trực tiếp tại `:6351–6353`.

User đăng nhập có thể gửi feedback với `page` chứa HTML có event handler. API lưu nguyên trường này; trang admin đưa `f.page` vào `innerHTML` mà không escape. Khi admin mở feedback, JavaScript của người gửi chạy trong origin/phiên admin và có thể gọi API bằng quyền admin. Cookie HttpOnly không ngăn script cùng origin thực hiện request.

Bằng chứng: lần theo dữ liệu từ endpoint feedback đến chỗ render; chưa chạy exploit trong trình duyệt. Đề xuất dùng `textContent` hoặc `_escapeHtml` hiện có cho mọi trường người dùng, kể cả username trong bảng quản trị.

### R10 — P1: OAuth login state không gắn với trình duyệt khởi tạo

Vị trí: `src/universal_video_ai/web/app.py:1090–1093`, `:1109–1115`, `:1138–1139`.

State chỉ được lưu theo provider. Callback hợp lệ có thể được mở từ trình duyệt khác và thay cookie đăng nhập ở đó. Kẻ tấn công bắt đầu OAuth cho tài khoản của mình rồi dẫn nạn nhân mở callback chưa sử dụng có thể khiến nạn nhân làm việc trong tài khoản của kẻ tấn công; dữ liệu/credential lưu sau đó thuộc tài khoản này. State dùng một lần vẫn không chứng minh callback thuộc trình duyệt khởi tạo.

Bằng chứng: agent dùng provider giả và request có phiên nạn nhân; callback chấp nhận state do phía khác tạo rồi thay session. Đề xuất gắn state với nonce cookie ngắn hạn và xác minh trước exchange/set session.

### R11 — P1: OAuth bỏ qua cấu hình đóng đăng ký

Vị trí: `src/universal_video_ai/web/app.py:1126–1133`.

Khi đã có user và `OPEN_REGISTRATION=false`, callback OAuth vẫn tạo tài khoản mới và cấp credit cho identity chưa biết. Nhánh đăng ký mật khẩu có kiểm tra chính sách nhưng nhánh này không có. Cài đặt dự kiến chỉ cho tài khoản do admin tạo vẫn cho người ngoài gia nhập nếu cấu hình identity provider.

Bằng chứng: callback cô lập với provider giả tạo được user khi đăng ký đóng. Đề xuất kiểm tra cùng chính sách trước tạo user; tài khoản đã tồn tại vẫn được phép đăng nhập.

### R12 — P2: Một yêu cầu nạp tiền có thể được duyệt hai lần

Vị trí: `src/universal_video_ai/web/store.py:2227–2233`.

Hai request duyệt có thể cùng đọc `pending` trước transaction ghi. Cả hai cộng credit; cập nhật status không có điều kiện `status='pending'`. Double-click hoặc hai admin thao tác đồng thời đủ gây lỗi, dù endpoint đã kiểm tra quyền admin.

Bằng chứng: tái hiện SQLite tạm có đồng bộ luồng khiến một yêu cầu **50 credit** được cộng thành **100**. Đề xuất claim trạng thái pending bằng cập nhật có điều kiện trong cùng transaction với cộng credit; chỉ phía claim thành công được ghi số dư.

### R13 — P2: Upload video bỏ qua kiểm tra đủ credit

Vị trí: `src/universal_video_ai/web/app.py:5147–5153`.

`/api/upload-video` lưu file, tạo job, trừ credit rồi khởi chạy; không kiểm tra đủ số dư như nhánh URL. Tài khoản hết credit tiếp tục tạo công việc CPU/GPU/provider tốn tài nguyên.

Bằng chứng: route cô lập nhận upload trong RAM, worker giả; số dư **0** vẫn được chấp nhận và thành **−1** khi giá job là 1. Đề xuất giữ chỗ credit nguyên tử trước lưu/tạo job, xử lý hoàn tiền khi thất bại và áp dụng cùng chính sách cho mọi nguồn đầu vào.

### R14 — P1: Startup recovery tác động cả công việc của worker còn sống

Vị trí: `src/universal_video_ai/web/store.py:2732–2737`; `src/universal_video_ai/web/app.py:354–357`, `:7531–7541`.

Mỗi startup đổi toàn bộ scheduled post `processing` thành `error`, không xác định ownership/lease. Với nhiều worker hoặc rolling restart, một worker mới có thể làm hỏng trạng thái upload đang chạy ở worker khác. Ngay cả một worker cũng claim đến 20 post rồi chạy tuần tự: restart giữa post đầu đánh lỗi cả các post chưa upload. Recovery localization tương tự đánh lỗi/hoàn tiền toàn bộ job đang queued/running.

Bằng chứng: database tạm cho thấy claim chưa thực thi thành lỗi, job localization đang hoạt động bị fail/refund. Đề xuất lease/worker ID và chỉ recover lease hết hạn; claim theo sức chứa thực thi. Giữ trạng thái chưa xác định cho upload có thể đã tạo kết quả từ xa.

### R15 — P1: Job Facebook dùng Page hiện được chọn thay vì Page đã lưu

Vị trí: `src/universal_video_ai/channel_agent/facebook_publishing.py:139–161`.

Queue cho Page A, sau đó đổi connection sang Page B: `upload()` đọc account hiện tại và gửi tới B, không so với destination trong job. Kiểm tra owner chỉ xác định user, không xác định đúng Page. Nội dung tự động có thể lên nhầm Page trong khi hồ sơ job vẫn chỉ A.

Bằng chứng: agent queue `page-123`, đổi Page rồi bắt được request giả tới `/page-456/videos`. Đề xuất xác thực danh tính destination đã lưu trước upload, giữ một snapshot account/token nhất quán trong toàn bộ thao tác và yêu cầu xử lý rõ khi connection thay đổi.

### R16 — P1: Hai lần run/retry có thể upload trùng một publishing job

Vị trí: `src/universal_video_ai/channel_agent/production_publishing.py:234–245`; luồng Facebook tương tự tại `facebook_publishing.py:332–344`.

Hai request cùng đọc job trước khi upload session được lưu đều có thể tạo session/upload riêng. Idempotency khi tạo bản ghi job không tuần tự hóa việc thực thi. Không có persisted claim bảo vệ trước hiệu ứng bên ngoài.

Bằng chứng: hai lần run đồng thời với provider giả tạo **hai session và hai upload** cho một job. Đề xuất claim nguyên tử hoặc execution lease trước gọi provider, bảo vệ cả run/retry/recovery và đối chiếu session khi phục hồi. Đây là lỗi riêng với race trong Redis: sửa Redis không khóa được hai request trực tiếp.

### R17 — P1: Timeout Facebook không rõ kết quả bị retry như upload mới

Vị trí: `src/universal_video_ai/channel_agent/facebook_publishing.py:339–360`.

Nếu Facebook nhận upload nhưng response mất trước khi lưu external ID, exception đặt job thành `failed`. Retry thấy ID rỗng nên upload mới và có thể tạo bài trùng. Đây là trạng thái kết quả từ xa chưa biết, không phải bằng chứng upload chưa xảy ra.

Bằng chứng: stub nhận upload rồi ném timeout; retry gọi upload lần nữa. Đề xuất trạng thái `outcome_unknown`, lưu session có khả năng phục hồi và reconcile trước khi cho upload mới. Chặn retry mù khi chưa xác định được kết quả.

### R18 — P2: Ngân sách provider mất phần đã tiêu khi stage thất bại

Vị trí: `src/universal_video_ai/channel_agent/automation.py:412–419`; `_consume_provider_budget` và `_execute_stage` trong cùng file.

Budget được tăng trong `refs` nhưng chỉ lưu refs ở nhánh thành công. Stage ném lỗi khiến handler chỉ lưu error; lần retry tải lại số cũ. Call thất bại vẫn có thể đã tiêu quota/chi phí, nên giới hạn trở nên không đáng tin cậy khi lỗi lặp lại.

Bằng chứng: giới hạn `max_external_api_calls=1` vẫn cho hai lần thử thất bại; persisted usage không giữ lần đã tiêu. Đề xuất lưu reservation trước thực thi và giữ số dùng qua thất bại/restart, đối soát theo lần gọi thực tế.

### R19 — P2: Draft dry-run khiến automation coi publish đã xong

Vị trí: `src/universal_video_ai/channel_agent/automation.py:164–173`.

Kiểm tra output tái sử dụng chấp nhận mọi publishing job không cancelled cho platform cần chạy, gồm draft dry-run và failed. Handler publish cũng dùng tiêu chí tương tự. Automation có thể bỏ qua publish rồi báo hoàn tất mà không upload.

Bằng chứng: một draft dry-run có sẵn làm publish stage hoàn thành mà không gọi handler. Đề xuất kiểm tra đúng render/destination và trạng thái hoàn tất từ xa; resume job dở dang rõ ràng; loại draft khỏi kết quả publish thành công.

### R20 — P1: Che phụ đề OCR có thể làm mất luôn toàn bộ phụ đề dịch

Vị trí: `src/universal_video_ai/orchestrator/service.py:1372–1379`, `:1515–1517`, `:2048–2052`; `render/renderer.py:525`.

Khi bật tạo phụ đề và text cover, OCR tạo overlay thành công và không bật karaoke animation, finalization chuyển overlay thành box có `text=''`. Sau đó logic lọc caption chỉ kiểm tra thời gian overlap, coi box rỗng đã hiển thị bản dịch và bỏ ASS cue. Renderer không vẽ chữ khi `text` rỗng: video có thể chỉ còn ô che phụ đề.

Bằng chứng: lần theo cả nhánh tạo box, lọc ASS và render; chưa render video thật cho trường hợp này. `skip_srt_when_text_overlays_present` bật mặc định; nhánh karaoke có ngoại lệ. Đề xuất chỉ coi overlay có text là caption đã có, luôn giữ ASS cho cleanup box.

### R21 — P1: Dựng track TTS chặn event loop của web

Vị trí: `src/universal_video_ai/orchestrator/service.py:2008–2011`, `:2031`; caller `web/app.py:1578`.

Trong coroutine localization, scheduling gọi probe/chuyển audio đồng bộ, `build_dubbed_track` chạy FFmpeg đồng bộ; còn các bước mix đồng bộ trong finalize. Web trực tiếp await localization trong asyncio task, không offload toàn bộ luồng. Với video dài/nhiều câu, request khác, heartbeat và lệnh cancel phải đợi subprocess kết thúc.

Bằng chứng: xác minh call chain và subprocess đồng bộ; chưa đo độ trễ tải thật. Đề xuất offload phần blocking bằng thread/process hoặc subprocess async, kiểm tra cancel giữa các batch và giới hạn số job tài nguyên nặng.

### R22 — P2: Cache Demucs có thể lấy stem của video khác cùng tên

Vị trí: `src/universal_video_ai/audio/demucs.py:329–335`; shared cache được cấu hình tại `audio/factory.py:116–118`.

Cache tìm output theo model và basename audio. Hai input khác thư mục nhưng cùng tên `video.wav` có thể nhận cùng stem cũ; kiểm tra file tồn tại/không rỗng không xác minh nội dung nguồn. Tách input directory theo job không giải quyết shared output cache này.

Bằng chứng: input thứ hai khác nội dung vẫn nhận byte stem của job đầu trong tái hiện cô lập. Đề xuất key theo hash nội dung nguồn cùng model/options, completion manifest và ghi cache nguyên tử.

### R23 — P2: Mix audio cắt phần lời TTS vượt thời lượng gốc

Vị trí: `src/universal_video_ai/mixer/service.py:165`; `orchestrator/service.py:2030`.

Scheduler cho phép kéo dài dubbed track để giữ câu cuối, nhưng mix với audio gốc dùng `amix:duration=first`. Nếu TTS vẫn dài hơn nguồn sau nén cho phép, output bị cắt ở cuối audio gốc dù kiểm tra coverage trước đó đạt. Nhánh nền thay thế cũng cần truyền thời lượng thực thay vì mặc định thời lượng nguồn.

Bằng chứng: FFmpeg offline với nguồn **1 giây**, dub **2 giây** cho mix **1 giây**. Đề xuất truyền playback duration xuyên suốt mixing/rendering và xử lý video/padding phù hợp, hoặc từ chối overflow để sửa script thay vì xuất thiếu lời.

### R24 — P2: Cache speech bỏ qua nội dung giữa file

Vị trí: `src/universal_video_ai/speech/service.py:45–49`.

Fingerprint chỉ gồm kích thước và 1 MiB đầu/cuối. Hai recording cùng độ dài, chung intro/outro nhưng thoại giữa khác nhau có cùng cache key, trả transcript/timing sai từ shared cache. Đây là bỏ sót dữ liệu đầu vào, không cần collision của SHA-256.

Bằng chứng: hai file 3 MiB có đầu/cuối giống, giữa khác tạo key giống nhau. Đề xuất stream-hash toàn bộ audio và giữ model/language/options trong key; tránh fallback chỉ theo filename khi không đọc được nội dung.

### R25 — P1: Chế độ MOCK không chặn LLM HTTP và bộ đếm không thấy call

Vị trí: `src/universal_video_ai/content_os/agents/llm_router.py:100–105`, `:258–262`; `tests/content_os/test_agents.py:45`.

Router gọi SDK/`requests.post` trực tiếp, bỏ qua execution guard/cost tracking. Với `PROVIDER_EXECUTION_MODE=MOCK`, các test có tên mock vẫn có thể gọi provider với cấu hình/key runtime và timeout 300 giây. `LIVE_PROVIDER_CALLS=0` không bảo đảm không có HTTP hay không tiêu quota.

Bằng chứng: patch HTTP transport bằng exception có kiểm soát; router MOCK vẫn gọi `requests.post` **1 lần**, trong khi `live_calls` vẫn **0**. Một lượt pytest ngoài sandbox đã đi tới HTTP thật ở dòng 258 và bị dừng; không thể dùng bộ đếm để xác nhận lượt đó không tiêu quota. Lượt kiểm thử tiếp theo dùng harness chặn TCP/DNS.

Đề xuất đưa mọi call qua `execute_provider_call`, khóa transport trong test và kiểm tra không có request khi MOCK; credential test cũng phải tách khỏi runtime.

### R26 — P1: Lỗi audit trở thành PASS

Vị trí: `src/universal_video_ai/content_os/agents/content_audit_agent.py:102–123`.

Output audit lỗi schema, kể cả decision `BLOCKED` với issue thiếu trường, rơi vào exception rồi được thay bằng `PASS`, `issues=[]`. Lỗi mạng/provider ở router trả `raw_content`; nhánh khác lại chuyển thành audit mock PASS. Vì vậy lỗi kiểm tra chất lượng làm workflow đi tiếp thay vì dừng.

Bằng chứng: xác minh nhánh xử lý output và caller workflow; chưa chạy tái hiện workflow đầy đủ. Đề xuất fail closed: giữ lỗi audit, chặn run hoặc yêu cầu đánh giá lại; chỉ cho phép audit giả trong MOCK được xác định rõ.

### R27 — P1: Đang chờ duyệt thủ công nhưng run bị đánh dấu completed

Vị trí: `src/universal_video_ai/content_os/workflow.py:147–152`; nhánh chờ tại `:289–292`, điều kiện resume tại `:1148`.

Với `auto_approve=False`, `_execute_workflow` trả `awaiting_approval`; `start_run` vẫn lưu completed, 100% và completed_at. API sử dụng cấu hình này. `resume_run` từ chối status completed; approval chỉ đổi stage không khôi phục status đúng.

Bằng chứng: đối chiếu caller và state transition. Đề xuất giữ status trả về từ workflow, chỉ ghi trường hoàn tất sau bước cuối, và kiểm tra cả chu trình chờ → duyệt → tiếp tục.

### R28 — P1: Script sửa đã được duyệt không phải script chuyển sang sản xuất

Vị trí: `src/universal_video_ai/content_os/workflow.py:263–266`, mapping artifact tại `:1096–1098`; caller `content_os/pipeline_adapter.py`.

Sau `PASS_WITH_FIXES`, revised script chỉ thay `context['script']` và được lưu trong `REVISION_REPORT`. Artifact `SCRIPT` gốc không được tạo version mới. Adapter và continuation API đọc `SCRIPT`, nên nội dung cũ đi vào sản xuất dù bản sửa mới vượt re-audit.

Bằng chứng: lần theo mapping lưu artifact và hai đường handoff. Đề xuất lưu revision được chấp nhận thành version SCRIPT mới và gắn approval/handoff với artifact version/checksum cụ thể.

### R29 — P1: Output validation thất bại vẫn hoàn tất sản xuất

Vị trí: `src/universal_video_ai/content_os/workflow.py:367–371`; `_validate_output` tại `:873–918`.

Validator trả `valid=False` cho output thiếu/hỏng, sai resolution hoặc duration. Caller lưu result nhưng vẫn chuyển stage sang COMPLETED; `start_run` lưu thành công. State machine/API không kiểm tra bool này, khiến job thành công dù artifact không dùng được.

Bằng chứng: xác minh giá trị trả về và nhánh chuyển stage; chưa chạy render thật. Đề xuất chỉ hoàn tất khi `valid=True`, giữ issue trong trạng thái failed/needs_review và chặn publish/handoff của output lỗi.

### R30 — P2: Timeout collector trả slot nhưng thread vẫn chạy

Vị trí: `src/universal_video_ai/analytics/youtube_research/collector.py:68–77`.

`wait_for(to_thread(...))` hết timeout chỉ hủy await, không dừng extraction thread đã chạy. Thoát semaphore trả slot cho lần tiếp theo trong khi extraction cũ còn tiếp tục. Timeout lặp lại vượt `max_concurrency`, tích lũy worker/network/subprocess; khóa theo project ở API cũng được nhả khi timeout. Socket timeout của yt-dlp không giới hạn toàn bộ extraction nhiều request.

Bằng chứng: phân tích cơ chế timeout và phạm vi semaphore/caller; chưa chạy stress test. Đề xuất giữ slot đến khi thread kết thúc hoặc dùng process có hard deadline/cancel; giới hạn tổng worker còn sống.

## Ưu tiên xử lý

Tổng cộng **30 phát hiện: 18 P1, 12 P2**. Thứ tự triển khai sửa nên dựa theo luồng được bật:

1. R09–R11: XSS và chính sách/phiên OAuth; R01: secret trong image.
2. R25–R29: khóa provider MOCK, audit/approval/validation và đúng version artifact.
3. R14–R17, R03–R05: quyền sở hữu execution, destination, phục hồi và chống đăng trùng.
4. R02, R08, R12–R13, R18: dữ liệu bền vững, số dư và ngân sách.
5. R20–R24, R30: đúng nội dung media, thời lượng và giới hạn tài nguyên; R06–R07, R19: retry/priority/completion.

## Kiểm chứng và giới hạn

Các tái hiện dùng dữ liệu tạm, fake Redis/HTTP và FFmpeg offline. Phát hiện chỉ truy vết mã đã được phân biệt rõ với phát hiện có tái hiện. Chưa xác minh hợp đồng API thực tế, GPU load, browser exploit hoặc vận hành nhiều process thật.

- Parse AST **244 file Python** trong `src` và `scripts`: không có lỗi cú pháp.
- `node --check` cho **11 file JavaScript**: đạt; `node --test tests/*.js`: **5/5 đạt**.
- Kiểm thử tập trung `test_redis_job_queue.py`, `test_redis_job_worker.py`, `test_job_queue.py`, `test_jobs_service.py`, `test_database.py`: **25 passed**, 1,82 giây, cost report bằng 0. Các tái hiện R03–R08 chứng minh những ca thiếu trong bộ test xanh này.
- Agent automation báo **55 targeted tests passed** và 5 tái hiện cô lập; agent Content OS không hoàn tất lượt targeted, nên không có tuyên bố test đạt cho toàn bộ scope đó.
- Pytest thu thập **1.195 test**. Lượt đầy đủ bị giới hạn 180 giây tại `test_history_bulk_rerun.py` sau hơn 54%; lượt bỏ nhóm này vượt 66% rồi bị giới hạn 180 giây khi Playwright đang khởi tạo. **Không có kết luận toàn bộ suite đạt.** Stack timeout ở Store/Playwright là quan sát kiểm thử, chưa đủ phân loại thêm lỗi production.
- Quan sát một test thất bại: `test_edge_catalog_contains_only_two_native_vietnamese_speakers`, catalog trả rỗng thay vì hai speaker mong đợi. Chưa tách hết ảnh hưởng môi trường/CLI nên không tính là phát hiện thứ 31.
- Môi trường kiểm thử: Python **3.14.4**; CI khai báo **3.11/3.12**. Không coi kết quả này thay thế việc kiểm thử đúng phiên bản production.
- TestClient treo trong sandbox kể cả ứng dụng FastAPI rỗng, nên các lượt HTTP test được chạy ngoài sandbox. Sau khi phát hiện R25, harness `/tmp/uvai_offline_pytest.py` chặn TCP/DNS trong tiến trình Python; cơ chế này không tự bảo đảm chặn mạng cho mọi executable con. Các lượt có timeout đã kết thúc, không có background pytest được cố ý để chạy tiếp.

Log kiểm tra tại thời điểm bàn giao:

- `/tmp/uvai-code-review-targeted.log`
- `/tmp/uvai-code-review-node.log`
- `/tmp/uvai-code-review-pytest-offline.log`
- `/tmp/uvai-code-review-pytest-offline-excluding-bulk.log`

File được tạo cho yêu cầu này trong repository: **chỉ báo cáo này**. Mã ứng dụng và các thay đổi có sẵn được giữ nguyên; không commit/deploy/publish.
