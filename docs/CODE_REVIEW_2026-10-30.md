# Rà soát mã nguồn — 2026-10-30

> Báo cáo này ghi nhận trạng thái **trước đợt sửa**. Các thay đổi và kết quả
> kiểm chứng sau sửa được ghi tại [REVIEW_FIXES_2026-10-30.md](REVIEW_FIXES_2026-10-30.md).

**Kết luận: REQUEST CHANGES trước khi vận hành tự động lâu dài.**
Ghi nhận **63 phát hiện: 26 P1, 37 P2**, gồm một lỗi P2 ở API thư viện chưa thấy
production caller (F63). Có các lỗi cũ vẫn còn và lỗi mới; không tính lại những
phát hiện cũ đã được sửa. Mỗi mục bên dưới có trigger, tác động và hướng sửa.

## Phạm vi và phương pháp

Rà soát working tree hiện tại, gồm thay đổi chưa commit và file mới, đối chiếu
`docs/CODE_REVIEW_2026-10-29.md` và các tối ưu ngày 30-10. Phạm vi mã ứng dụng
là `src/`, script vận hành, Docker/Compose, packaging và cấu hình CI; test được
dùng để kiểm chứng. Không kiểm toán thư viện cài sẵn, dữ liệu media hay binary
build. `license_server/` và bản Windows staging hiện không có nguồn Python.

Đã đọc cấu hình `/home/dai/.codex/agents/code-reviewer.toml`. Lời gọi agent type
`code-reviewer` bị công cụ từ chối (`unknown agent_type`); bốn agent mặc định
được giao đọc và áp dụng nguyên vai trò trong cấu hình này: web/bảo mật;
publishing/automation; media/network/cache; Content OS/analytics/reup. Agent
chính kiểm tra queue, database legacy, triển khai và các module dùng chung.

Đối chiếu reviewer prompt, constitution, dependency graph, module map, roadmap
và checklist trong `PROJECT_BRAIN/`. Ưu tiên lỗi hành vi thay vì quy tắc định dạng.
Mỗi phát hiện ghi điều kiện và bằng chứng; lỗi được tái hiện với fake transport,
fake Redis hoặc SQLite tạm được phân biệt với kết luận từ đọc mã.

P1: cần xử lý trước khi bật luồng vận hành liên quan. P2: ảnh hưởng tính đúng,
hiệu năng hoặc khả năng phục hồi. Không sửa mã ứng dụng trong lượt review này.

## Queue, database và triển khai

### F01 — P1: Docker build đóng gói secret và dữ liệu runtime

Vị trí: `Dockerfile:18`; không có `.dockerignore` ở gốc.

`COPY . .` đưa `.env`, virtualenv, cookie, database, cache và output trong build
context vào image. Khi build từ workspace đang vận hành, điều này vừa làm lộ
credential cho người đọc image/cache, vừa tăng thời gian build và dung lượng đĩa.
Xác minh tĩnh cấu hình và sự tồn tại `.env`, không đọc giá trị secret. Khuyến nghị
lọc build context và chỉ COPY thành phần cần chạy. R01 cũ vẫn còn.

### F02 — P1: Script đóng gói Windows sao chép `.env` thật

Vị trí: `prepare_for_windows_build.sh:21`, `build_exe.bat:48–53`,
`build_nuitka.bat:82–87`.

Các script ưu tiên sao chép `.env` của máy phát triển vào thư mục staging/dist
hoặc file cấu hình đi kèm executable. Chia sẻ output theo hướng dẫn sẽ chia sẻ
API key, session secret và credential cấu hình trong đó. Đây là luồng khác với
`packaging/build_windows.ps1` có bước loại runtime data. Xác minh tĩnh; chưa chạy
build. Dùng cấu hình mẫu và tạo secret riêng khi cài đặt.

### F03 — P1: Database bot nằm ngoài volume bền vững

Vị trí: `docker-compose.prod.yml:7–20`.

Bot ghi `/app/database.sqlite3`, trong khi volume chỉ gắn ở `/app/data`.
Recreate container làm mất database writable layer, gồm credit và audit.
Restart cùng container không tái hiện điều này. Xác minh tĩnh; R02 cũ vẫn còn.
Đổi đường dẫn, backup/migrate dữ liệu cũ và kiểm tra recreate.

### F04 — P2: Redis trong Compose không khớp cổng client

Vị trí: `docker-compose.prod.yml:17–18`, `:55–57`, `:81–82`.

App/web dùng host network và kết nối `localhost:6379`; Redis bridge chỉ publish
host port `6380`. Khi host không có Redis riêng ở 6379, hai client không kết nối
được Redis của stack và có thể rơi vào cache RAM riêng từng process; nếu host có
Redis ở 6379, chúng kết nối nhầm instance. Health check Redis vẫn xanh. Xác minh
tĩnh, chưa chạy Compose. Thống nhất host port hoặc mạng/DNS service.

### F05 — P1: Hai Redis consumer nhận cùng job

Vị trí: `src/universal_video_ai/jobs/queue.py:240–253`.

`ZRANGE` và `ZREM` tách rời; kết quả `ZREM` không được kiểm tra. Hai consumer cùng
đọc pending đầu tiên đều trả envelope và chạy render/trừ tiền/upload hai lần.
Tái hiện fake Redis với barrier: hai lần dequeue đều trả `one`.
Claim nguyên tử và bảo vệ hiệu ứng bên ngoài bằng idempotency. R03 cũ vẫn còn.

### F06 — P1: Worker crash khiến job mất khỏi pending vĩnh viễn

Vị trí: `src/universal_video_ai/jobs/queue.py:246–253`, `jobs/worker.py:79–83`.

Dequeue xóa pending trước thực thi, không có lease/ack/reclaim. Sau tiến trình
chết, worker mới không tìm thấy công việc dù hash vẫn `running`. Tái hiện fake
Redis: dequeue kế tiếp là `None`, trạng thái `running`. Thêm ownership/lease và
recovery; retry exception không giải quyết process crash. R04 cũ vẫn còn.

### F07 — P2: Retry hồi sinh job đã hủy

Vị trí: `src/universal_video_ai/jobs/worker.py:84–96`, `jobs/queue.py:206–224`.

Nếu cancel đến khi callback đang chạy rồi callback ném lỗi, nhánh exception
requeue vô điều kiện và ghi `pending` lên `cancelled`. Tái hiện callback cancel
rồi ném `OSError`: job trở lại pending và dequeue được. Dùng chuyển trạng thái
có điều kiện/nguyên tử ở retry và completion. R05 cũ vẫn còn.

### F08 — P2: Wrapper JobService nuốt exception nên queue không retry

Vị trí: `src/universal_video_ai/jobs/service.py:122–138`.

Callback lỗi được chuyển thành FAILED rồi wrapper trả về bình thường, nên queue
coi đã thành công. Tái hiện callback `OSError`: gọi một lần dù `max_retries=3`.
Lớp service cũng bỏ qua kết quả enqueue False, có thể để job pending mãi khi
queue đã dừng. Quy định một lớp sở hữu retry và chỉ thất bại cuối sau hết lượt.
R06 cũ vẫn còn.

### F09 — P2: Hàng đợi RAM thực thi LOW trước URGENT

Vị trí: `src/universal_video_ai/jobs/queue.py:291–297`, `:375–381`.

PriorityQueue lấy số nhỏ nhất nhưng LOW=1, URGENT=4. Cùng backlog, low chạy
trước urgent; job cùng priority còn được sắp theo job_id thay vì thời gian tạo.
Tái hiện enqueue urgent/low trước consumer: phần tử đầu là `low`. Đổi thứ tự
priority và dùng sequence FIFO; kiểm tra thứ tự thực thi thực tế. R07 cũ vẫn còn.

### F10 — P2: Hai kết nối DB có thể cùng chi vượt số dư

Vị trí: `src/universal_video_ai/database/manager.py:615–632`, `:635–649`.

RLock chỉ bảo vệ từng instance; thao tác đọc rồi ghi số dư tuyệt đối không bảo vệ
nhiều connection/process. Tái hiện SQLite tạm: số dư 3, hai lần trừ 2 đều True,
số dư cuối 1 nhưng total_used=4. Dùng UPDATE tương đối có điều kiện credits >=
amount, kiểm tra rowcount và ghi audit trong cùng transaction. R08 cũ vẫn còn;
không khẳng định mọi bot đơn instance đều gặp race này.

### F11 — P2: Kết nối SQLite tồn tại đến khi GC thu hồi

Vị trí: `src/universal_video_ai/cache/sqlite_cache.py:42–59`, `:64–76`;
`src/universal_video_ai/web/store.py:1531–1535` và các caller dùng `with`.

Connection context manager chỉ commit/rollback transaction; mã không close
connection khi thoát block. Cache và Store mở connection mới cho từng thao tác,
khiến file descriptor/bộ nhớ phụ thuộc lịch GC. Tái hiện cache SQLite/WAL trên
Linux, chủ động hoãn GC: 100 set làm FD tăng 4 → 205; sau gc.collect giảm về 4.
Đây là tích tụ đến GC, không phải khẳng định connection vĩnh viễn không được
thu hồi. Đóng connection trong finally/contextmanager riêng, giữ nguyên ranh
giới transaction. Agent web kiểm tra thêm Store: 100 read, GC hoãn, FD 4 → 104,
sau collection về 4. Cần áp dụng ở các caller Store dùng cùng mẫu.

### F12 — P2: TrendScanner bỏ qua engagement và lỗi ngày có timezone

Vị trí: `src/universal_video_ai/trends/service.py:91–97`;
`trends/scoring.py:44–61`.

Caller gọi calculate_trend_score(item) không truyền max_views/likes/comments,
nên mọi thành phần engagement bằng 0. Với published_at ISO có Z/+00:00, phép
trừ datetime.utcnow() naive với datetime aware ném lỗi rồi bị nuốt, làm recency
cũng bằng 0. Tái hiện hai video 1 view/1.000.000 view có ngày UTC hợp lệ: cùng
điểm 0. Khi source không đưa score sẵn, ranking trở thành thứ tự đầu vào.
Tính normalization từ tập ứng viên và chuẩn hóa datetime về cùng timezone.

## Publishing, automation và bot

Các tái hiện F13–F26 do agent publishing thực hiện với SQLite tạm và provider giả.

### F13 — P1: Facebook gửi tới Page đang chọn thay vì Page đã lưu trong job

Vị trí: `channel_agent/facebook_publishing.py:139–161` (dưới `src/universal_video_ai/`).
Queue cho A rồi đổi connection sang B: job vẫn ghi A nhưng request gửi tới B.
Tái hiện lưu `page-123`, POST tới `/page-456/videos`. Xác minh destination đã lưu
và giữ một snapshot account/token trong cả upload. R15 cũ vẫn còn.

### F14 — P1: Hai lần run tạo hai upload cho cùng publishing job

Vị trí: `channel_agent/production_publishing.py:241–245`;
`channel_agent/facebook_publishing.py:338–344`.
Hai caller cùng thấy session/external ID rỗng đều thực hiện upload. Tái hiện
YouTube: hai session và hai upload cho một job. Claim thực thi bền vững/nguyên tử
trước gọi provider, bao phủ cả run/retry/recovery. R16 cũ vẫn còn.

### F15 — P1: Facebook timeout có kết quả chưa rõ bị retry thành upload mới

Vị trí: `channel_agent/facebook_publishing.py:344–360`.
Nếu server đã nhận video nhưng response mất, job bị failed và không có ID; retry
upload lại. Provider giả nhận upload rồi ném timeout đã nhận hai upload. Giữ trạng
thái outcome-unknown và đối soát trước tạo upload mới. R17 cũ vẫn còn.

### F16 — P2: Stage lỗi làm mất ngân sách đã sử dụng

Vị trí: `channel_agent/automation.py:265–267`, `:408–419`.
Usage trong refs chỉ lưu sau thành công; exception bỏ mất reservation vừa tăng.
Tái hiện max_external_api_calls=1: advance rồi retry thực hiện hai lần lỗi,
refs persisted vẫn rỗng. Lưu reservation trước gọi provider và giữ usage khi lỗi,
retry/restart. R18 cũ vẫn còn.

### F17 — P2: Draft/failed publishing job được coi là publish đã hoàn tất

Vị trí: `channel_agent/automation.py:164–173`;
`web/channel_agent_router.py:565–570`.
Chỉ lọc platform và khác cancelled, bỏ qua render/destination/kết quả từ xa.
Tái hiện draft dry-run khiến automation completed với 0 lần gọi publish handler.
Chỉ tái sử dụng kết quả đúng artifact/destination đã hoàn thành; resume job dở
dang theo trạng thái cụ thể. R19 cũ vẫn còn.

### F18 — P1: Advance thứ hai phục hồi nhầm stage của request còn sống

Vị trí: `channel_agent/automation.py:342–348`.
Mỗi advance đổi tất cả step running thành pending, không kiểm tra ownership hay
lease. Giữ request đầu ở research rồi gọi advance thứ hai đã thực thi cùng stage
hai lần; render/publish cũng qua cơ chế này. Claim stage nguyên tử và chỉ recover
lease hết hạn. Đây là race khác với F14 ở lớp publishing.

### F19 — P2: Cancel đang chạy bị completion ghi đè

Vị trí: `channel_agent/automation.py:427–444`.
Chỉ kiểm tra cancel ở đầu advance. Cancel trong handler rồi handler thành công
vẫn tiếp tục cập nhật run; tái hiện cancelled chuyển thành queued. Kiểm tra lại
trạng thái persisted giữa stage và cập nhật có điều kiện theo execution claim.

### F20 — P2: Budget tính theo stage thay vì số lời gọi provider

Vị trí: `channel_agent/automation.py:294–310`;
`web/channel_agent_router.py:536–542`.
Một reservation assets-stage cho phép sinh visual, voice, metadata và thumbnail.
Với max_llm_calls=1, handler/ProductionAssetService thật gọi fake LLM bốn lần
nhưng chỉ ghi llm_calls=1. Thực thi budget tại từng provider operation, kể cả
continuation/retry, thay vì chỉ bọc ngoài stage.

### F21 — P2: Tất cả upload thất bại nhưng automation vẫn completed

Vị trí: `web/channel_agent_router.py:593–602`.
Handler bắt lỗi upload rồi trả mapping bình thường; orchestrator coi mapping là
thành công. Tái hiện YouTube job failed, run completed. Truyền lỗi tổng thể lên
và biểu diễn partial/incomplete để bounded retry còn hoạt động. Khác F17: lỗi
này xảy ra ngay khi upload mới thất bại, không cần prior job.

### F22 — P2: Crash giữa tạo cycle và liên kết automation làm kẹt cycle

Vị trí: `channel_agent/autonomous_operator.py:350–354`, `:370–380`.
Cycle commit trước khi tạo/link automation run. Giả lập ngắt ở giữa để lại
automation_run_id=None; cùng idempotency key trả cycle hỏng, advance TypeError,
cycle mới bị concurrency limit chặn. Khi gặp cycle cũ, sửa linkage chưa hoàn tất
bằng automation key xác định trước khi trả về.

### F23 — P2: Scheduler dời lịch trước khi tạo cycle bền vững

Vị trí: `channel_agent/autonomous_operator.py:473–484`.
Crash sau UPDATE next_run_at trước start_cycle làm mất lượt chạy, kể cả run_once.
Tái hiện không tạo cycle nào, tick kế tiếp không làm gì, lịch đã dời 19.600 giây.
Lưu occurrence/cycle trước dời lịch và dùng idempotency để phục hồi.

### F24 — P2: Hai start_cycle vượt max_concurrent_cycles

Vị trí: `channel_agent/autonomous_operator.py:355–362`.
Kiểm tra admission và insert ở transaction khác nhau. Tái hiện limit=1 nhưng hai
request đồng thời tạo hai cycle running. Serialize kiểm tra và insert trong cùng
write transaction; áp dụng cả hạn mức ngày/tuần có cùng mẫu.

### F25 — P2: Lệnh Telegram có hậu tố tên bot không được dispatch

Vị trí: `bot/real_telegram_adapter.py:106–112`.
`/credits@ReviewBot` được lookup nguyên `credits@ReviewBot`, trong khi handler
đăng ký là credits. Tái hiện không gọi handler và trả Unknown command. Chuẩn hóa
command bằng cách xử lý hậu tố bot trước dispatch.

### F26 — P2: Lỗi gửi video Telegram thoát khỏi nhánh phục hồi của caller

Vị trí: `bot/real_telegram_adapter.py:85–87`, caller `bot/telegram_bot.py:392–402`.
Trong event loop đang chạy, send_video tạo task rồi trả ngay; lỗi upload xảy ra
sau đó không đến try/except của caller. Tái hiện nhận unhandled task error, không
có exception đồng bộ. Await delivery hoặc theo dõi task và xử lý lỗi/thông báo
cho user rõ ràng.

## Web và bảo mật

Các đường dẫn phần này nằm dưới `src/universal_video_ai/`. Agent web tái hiện
backend bằng file/SQLite/provider giả trong `/tmp`, frontend bằng trình duyệt.

### F27 — P1: Publishing pack rỗng cho phép tải file máy chủ

Vị trí: `web/app.py:7018–7026`, `:7032–7042`, `:6721–6727`.
Với job thuộc user nhưng chưa có pack, `Path("")` trỏ working directory. Endpoint
asset phục vụ file ngay trong thư mục đó; hai nhánh ZIP duyệt đệ quy và đóng gói
cả thư mục, có thể gồm secret/database và lượng media lớn. Agent tái hiện cả ba
handler bằng marker vô hại trong `/tmp`. Từ chối path vắng mặt trước khi tạo Path,
và ràng buộc resolved path vào pack directory được cấp riêng cho job.

### F28 — P1: Stored XSS chạy trong phiên admin

Vị trí: `web/static/app.js:6410–6417`, `:6351–6353`.
Feedback page và username được đưa vào HTML không escape. Payload từ user được
chạy khi admin mở dữ liệu, có quyền gọi API cùng origin. Agent tái hiện sink
trong trình duyệt. Dùng textContent/DOM properties hoặc escaping đúng context.
R09 cũ vẫn còn.

### F29 — P1: Identity OAuth state không gắn với trình duyệt khởi tạo

Vị trí: `web/app.py:1090–1093`, `:1109–1115`, `:1138–1139`.
Callback state hợp lệ từ phía khác thay session trong trình duyệt nạn nhân.
Provider giả tái hiện victim nhận signed session của attacker. Nonce cookie ngắn
hạn phải được kiểm tra trước exchange và set session. R10 cũ vẫn còn.

### F30 — P1: OAuth tạo user khi đăng ký đã đóng

Vị trí: `web/app.py:1126–1134`.
Nhánh tạo identity user bỏ qua policy của đăng ký mật khẩu. Tái hiện đăng ký đóng,
đã có user, identity mới vẫn được tạo và nhận 10 credit. Áp dụng cùng policy trước
tạo user; vẫn cho phép tài khoản đã tồn tại đăng nhập. R11 cũ vẫn còn.

### F31 — P1: Email provider chưa xác minh có thể đăng nhập tài khoản đã có

Vị trí: `web/identity_oauth.py:166–168`, lookup `web/app.py:1121–1125`.
GitHub fallback chọn emails[0] kể cả chưa verified, rồi local lookup tin email đó.
Provider giả trả email chưa verified đã lấy được session admin đang tồn tại.
Giữ verified status trong IdentityResult và chỉ link email đã xác minh; không
fallback sang email chưa xác minh khi tìm identity.

### F32 — P1: Tự liên kết theo email cho phép chiếm trước tài khoản SSO

Vị trí: `web/app.py:949–962`, `:1121–1125`, `:1135–1139`.
Khi đăng ký mở, attacker đăng ký email nạn nhân mà không xác minh. Nạn nhân SSO
sau đó đi vào cùng account, nhưng mật khẩu attacker vẫn hợp lệ. Agent tái hiện
cả hai cùng authenticate một user. Yêu cầu local email ownership đã xác minh hoặc
explicit linking trong phiên đã xác thực, thay vì tự gộp theo email.

### F33 — P1: Consent URL mạng xã hội gắn credential cho người khởi tạo link

Vị trí: `web/app.py:7691–7698`, `:7722–7739`.
Nếu nạn nhân mở consent URL/QR do attacker khởi tạo và chấp thuận provider,
credential tài khoản xã hội nạn nhân được lưu cho attacker theo state ban đầu.
Agent tái hiện bằng credential giả. Gắn luồng kết nối với owner đã xác thực và
yêu cầu owner xác nhận trước kích hoạt, kể cả khi hỗ trợ duyệt QR khác thiết bị.

### F34 — P1: Title trong lịch sử thực thi stored HTML

Vị trí: `web/static/app.js:2794–2797`; lưu title tại `web/app.py:1763`, `:1803–1806`.
Title dịch/sinh từ nguồn không tin cậy được nội suy vào HTML của lịch sử; refresh
có thể thực thi code trong phiên user. Agent tái hiện browser sink; đường dữ liệu
title production được lần theo tĩnh. Render title như text.

### F35 — P2: Escape cho text được dùng sai trong HTML attribute

Vị trí: `web/static/app.js:6420–6423`; caller `:3124`, `:3358–3359`.
_escapeHtml không escape quote, nhưng kết quả được nội suy trong attribute có quote.
Browser tái hiện title `A "quoted" title` bị cắt thành `A ` và speaker attribute
chèn thêm được thực thi. Tạo DOM và gán value/data properties trực tiếp hoặc dùng
escaping đúng attribute context.

### F36 — P2: Bốn nút hành động Content OS gọi hàm không tồn tại

Vị trí: `web/static/app.js:5763–5766`.
Handler của cả bốn nút row action không được định nghĩa. Agent browser tái hiện
ReferenceError trước khi gọi API. Gắn vào handler đã triển khai, truyền đúng row ID.

### F37 — P2: OAuth failure ghi app secret/code vào traceback log

Vị trí: `web/identity_oauth.py:205–212`, log tại `web/app.py:1116–1117`.
Facebook exchange đặt client_secret/code trong URL; raise_for_status tạo exception
có URL, được logger.exception ghi lại. Agent tái hiện bằng sentinel secret.
Sanitize ngoại lệ trước log và tránh đưa credential vào URL khi provider hỗ trợ.

### F38 — P2: Một yêu cầu nạp tiền được duyệt hai lần

Vị trí: `web/store.py:2227–2233`.
Hai request cùng đọc pending rồi cùng cộng credit; update không claim pending có
điều kiện trong cùng transaction. Tái hiện request 50 credit được cộng thành 100.
Claim nguyên tử trước cộng và chỉ caller claim thành công được ghi. R12 cũ vẫn còn.

### F39 — P2: Upload video bỏ qua kiểm tra đủ credit

Vị trí: `web/app.py:5128–5153`.
Upload lưu file/tạo job/trừ tiền mà không có admission gate tương ứng nhánh URL.
Tái hiện user 0 credit vẫn được nhận job và số dư -1. Giữ chỗ credit nguyên tử
trước chấp nhận công việc, kèm hoàn tiền phù hợp khi thất bại. R13 cũ vẫn còn.

### F40 — P1: Startup recovery đánh lỗi công việc của worker còn sống

Vị trí: `web/store.py:2290–2306`, `:2732–2737`;
caller `web/app.py:355–357`, `:7538–7541`.
Startup fail/recover tất cả active localization hoặc scheduled post processing,
không kiểm tra worker sở hữu. Rolling restart/multiple workers có thể đánh lỗi,
hoàn tiền hoặc làm sai trạng thái công việc đang thực thi ở worker khác. Hai
Store tạm tái hiện recovery làm active job và claimed post thành error. Dùng lease,
worker ID, claim theo sức chứa và chỉ recover lease hết hạn. R14 cũ vẫn còn.

## Content OS và reup

Các phát hiện F41–F48 được agent tái hiện với SQLite tạm và fake agents/validator.

### F41 — P1: Run chờ phê duyệt được ghi completed

Vị trí: `content_os/workflow.py:147–152`.
auto_approve=False trả awaiting_approval nhưng start_run ghi completed, 100% và
completed_at. Resume từ chối; approval chỉ đổi stage không sửa status. Tái hiện
đủ chu trình trên. Lưu trạng thái chờ và chỉ ghi completion sau sản xuất thành công.
R27 cũ vẫn còn.

### F42 — P1: Script sửa đã qua audit không phải script đưa vào production

Vị trí: `content_os/workflow.py:263–266`, `:1096–1098`;
`content_os/pipeline_adapter.py:88–94`.
Revision chỉ sửa context và REVISION_REPORT; adapter đọc SCRIPT artifact gốc.
Tái hiện re-audit REVISED APPROVED SCRIPT nhưng handoff ORIGINAL SCRIPT.
Lưu revision được chấp nhận thành SCRIPT version mới và gắn approval/handoff với
version/checksum đó. R28 cũ vẫn còn.

### F43 — P1: Output validation thất bại vẫn hoàn tất run

Vị trí: `content_os/workflow.py:367–374`.
valid=False được lưu nhưng không chặn stage completed. Tái hiện output invalid
vẫn trả completed; production caller chỉ kiểm tra file tồn tại. Yêu cầu valid=True
trước completion/handoff và lưu issue trong failed/needs-review. R29 cũ vẫn còn.

### F44 — P1: Manual production tự duyệt storyboard

Vị trí: `content_os/workflow.py:328–331`.
Production ghi approval `approved`, `Auto-approved` và chuyển assets vô điều kiện,
kể cả config auto_approve=False do API truyền. Tái hiện có approval này mà user
chưa duyệt. Tôn trọng manual mode và chỉ tiếp tục khi version storyboard hiện tại
đã được phê duyệt thực sự.

### F45 — P2: Production xong nhưng Content OS run vẫn running

Vị trí: `web/content_os_router.py:741–759`, `content_os/workflow.py:966–970`.
Background caller cập nhật linked job; stage advancement không cập nhật terminal
run status. Tái hiện production valid/completed nhưng persisted run running,
stage completed và thiếu completed_at; exception cũng chỉ cập nhật linked job.
Đồng bộ status/progress/timestamp/error của cả run và job khi kết thúc.

### F46 — P2: Reup cũ chờ duyệt làm đói công việc mới

Vị trí: `reup/service.py:129–132`.
active() luôn lấy 20 run cũ nhất, gồm ready chưa được duyệt, upload hẹn tương lai
và run bị skip do tắt tự động. Các mục này chiếm batch vô hạn. Tái hiện 20 ready
chưa duyệt cộng một queued mới: ba cycle vẫn 0 submission. Chọn actionable/due
work và dùng pagination/rotation công bằng.

### F47 — P2: Mất duration khiến reup bỏ qua giới hạn thời lượng

Vị trí: `reup/discovery.py:375–380`, `:678–679`.
Public metadata tạo candidate với duration mặc định 0 dù nguồn cung cấp duration;
filter bỏ qua 0 như unknown. Tái hiện Douyin 600 giây vẫn qua max_seconds=60.
Chuẩn hóa đơn vị duration và xác minh unknown duration trước tự enqueue công việc
có giới hạn, tránh tải/render video quá dài ngoài dự kiến tài nguyên.

### F48 — P2: Nguồn reup đã hủy không chọn lại được

Vị trí: `reup/service.py:93–96`.
Enqueue trả record nguồn cũ bất kể terminal status. Tái hiện chọn lại source đã
cancel nhận đúng cancelled run cũ; không có action khởi động lại, trái hướng dẫn
retry đang hiển thị. Hỗ trợ explicit new attempt/reset cho cancelled, đồng thời
giữ bảo vệ duplicate upload.

## Media và đối chiếu các tối ưu gần đây

Các đường dẫn dưới `src/universal_video_ai/`. Trừ F57 là phân tích tĩnh, các mục
trong phần này có tái hiện fake provider/worker hoặc FFmpeg với media cục bộ.

### F49 — P1: Tối ưu đọc WAV header đo sai file có độ dài unknown

Vị trí: `orchestrator/service.py:1691–1696`, `mixer/service.py:354–359`.
wave.open chấp nhận WAV có chunk length 0xffffffff và trả số frame quảng cáo,
không phải frame thực tế. Tái hiện audio 1 giây được đo 268.435,455875 giây,
trong khi ffprobe trả 1 giây; scheduler mặc định nén nó xuống 0,59275 giây.
Đây là regression của fast path đọc header vừa thêm, có thể làm sai lịch lời
thoại, gây mất lời/tốn xử lý. Kiểm tra độ dài header so với dữ liệu thực và probe
fallback với unknown-length WAV, thay vì tin mọi giá trị frames dương.

### F50 — P1: Rate limiter download có thể deadlock event loop

Vị trí: `downloader/rate_limiter.py:52–61`, nhánh per-user `:69–80`.
Coroutine await sleep khi đang giữ threading.Lock. Coroutine thứ hai lấy cùng
lock chặn event-loop thread, khiến coroutine đầu không thể tỉnh để nhả lock.
Tái hiện concurrent acquire tại request limit bị watchdog signal ngắt deadlock.
Nhả lock đồng bộ trước await rồi recheck/reserve capacity dưới lock phù hợp.

### F51 — P1: Whisper CPU recovery lấy lại chính lock không reentrant

Vị trí: `speech/whisper.py:271–276`, trigger `:317–318` dưới lock `:279–281`.
Nếu inference CPU/fp32 ném numeric error phù hợp, recovery quay về CPU dùng cùng
lock inference đang giữ. Fake torch/Whisper với ValueError invalid values in logits
tái hiện blocked acquisition, phải ngắt bằng watchdog. Chỉ fallback khi chuyển
device thực sự hoặc nhả inference lock trước chạy recovery.

### F52 — P1: Cleanup overlay rỗng vẫn loại toàn bộ caption dịch

Vị trí: `orchestrator/service.py:2065–2069`, clear text tại `:1375–1382`.
Ở nhánh phụ đề thường, overlay bị xóa text để chỉ che nguồn, nhưng caption coverage
chỉ xét thời gian overlap. Cue ASS bị bỏ, renderer chỉ vẽ box rỗng. Tái hiện một
empty overlay xóa cue duy nhất; đường finalization/render được xác minh tĩnh.
Chỉ overlay có bản dịch thực sự hiển thị mới được coi là caption coverage.
R20 cũ vẫn còn.

### F53 — P2: Cancel vẫn nhả capacity nặng trước khi thread kết thúc

Vị trí: `orchestrator/service.py:578–581`, mẫu tương tự ở download/OCR/render.
Cancel to_thread thoát semaphore context trong khi thread nền tiếp tục. Tái hiện
qua _prepare(): hủy transcription đầu cho phép transcription thứ hai chạy,
hai active workers dù limit mặc định một. Helper run_blocking_bounded mới giữ
đúng một trong phép thử tương đương, nhưng chưa áp dụng xuyên suốt. Giữ slot đến
khi công việc nền thực sự hoàn tất cho tất cả stage nặng.

### F54 — P2: Cancel trong rate-limit acquire làm mất slot vĩnh viễn

Vị trí: `downloader/rate_limiter.py:38–41`.
Semaphore được lấy trước throttle delay; caller chỉ bắt đầu cleanup sau khi
acquire trả về. Cancel giữa delay làm slot không được release. Tái hiện limiter
một slot còn 0 slot sau cancel. Nhả slot khi acquire thất bại/bị hủy, và phân biệt
ownership trước/sau acquire để không double-release.

### F55 — P2: Demucs cache nhận nhầm stem của nguồn khác cùng basename

Vị trí: `audio/demucs.py:329–335`; factory dùng shared cache.
Lookup theo model/basename không kiểm tra nội dung. Tái hiện video.wav thứ hai
khác nguồn nhận stem của video.wav đầu. Key theo content/options và validated
completion manifest, ghi output hoàn tất nguyên tử. R22 cũ vẫn còn.

### F56 — P2: Mix cuối cắt phần dubbed speech được scheduler giữ lại

Vị trí: `mixer/service.py:168–169`; duration nhánh replacement tại
`orchestrator/service.py:1426`, `:1437`.
amix duration=first giới hạn theo audio gốc; replacement cũng nhận source duration.
FFmpeg offline: nguồn 1 giây, dub 2 giây cho mix 1 giây. Truyền playback duration
xuyên suốt và định nghĩa video padding hoặc từ chối overflow. R23 cũ vẫn còn.

### F57 — P2: Music selection vẫn phân tích audio nặng trên event loop

Vị trí: `orchestrator/service.py:1393–1398`.
select_like chạy đồng bộ; AudioAnalyzer nạp đầy audio và tính spectral features
của nguồn/thư viện. Với librosa và thư viện đã có track, video dài có thể chặn
request/cancel và tăng RAM. Bằng chứng tĩnh, chưa benchmark tải thật. Offload
dưới bounded capacity, phân tích excerpt có giới hạn và cache features của track.
R21 chỉ được sửa một phần: scheduling/track assembly/mixing đã offload, còn nhánh này.

### F58 — P2: Downloader trả đường dẫn MP4 chưa chắc tồn tại

Vị trí: `downloader/ytdlp_downloader.py:182–184`.
Direct WebM/MKV không nhất thiết đi qua merger, nhưng code ép extension output
path thành .mp4. Fake yt-dlp tái hiện WebM thực tế bị báo thành path MP4; validation
kiểm tra file thiếu hoặc file cũ không liên quan. Dùng completed filepath thực tế
hoặc remux rõ ràng trước trả kết quả.

### F59 — P2: Split-screen dựng command FFmpeg sai

Vị trí: `postprocess/video_transform.py:188–199`.
Insert theo index tách -i khỏi filename, remove giá trị filter đầu tiên lấy nhầm
complex graph, graph còn thiếu label [out] được map. FFmpeg với media cục bộ thất
bại No such filter: '-map'. Dựng inputs/graph/maps rõ ràng; trả lỗi transform để
caller xử lý thay vì silently dùng render gốc.

### F60 — P2: Speed transform đổi video nhưng giữ nguyên audio

Vị trí: `postprocess/video_transform.py:163–164`, `:178–182`.
Video setpts thay tốc độ nhưng audio copy nguyên. Tái hiện input 2 giây, factor=2:
video 1,066667 giây, audio 2 giây. Preset Social Media có speed=1.01, tạo drift
tăng theo thời lượng. Áp dụng audio tempo tương ứng và quản lý output duration.

### F61 — P2: TTS cắt im lặng nội dung dài quá 4.096 ký tự

Vị trí: `tts/premium.py:87–91`.
Whole-text fallback/segment chỉnh dài chỉ gửi cleaned[:4096] nhưng trả synthesis
success. Fake transport: 5.000 ký tự thành 4.096 ký tự trong request. Chia thành
chunk giữ đủ nội dung hoặc báo lỗi input quá dài rõ ràng; không trả thành công cho
audio thiếu phần cuối.

### F62 — P2: Google translator thiếu cleanup client theo vòng đời job

Vị trí: `translate/translator.py:209–212`.
Localization tạo translator theo service/job nhưng đường này không đóng AsyncClient
được tạo lazy. Tái hiện fake-client: ba translation tạo ba client, 0 client đóng.
Thêm owned async cleanup trên cả success/failure/cancel. Chưa đo số socket thật
tích tụ; phát hiện là lifecycle không deterministic, không khẳng định socket leak
đã đo trên production.

### F63 — P2: Voice matching trả một ký tự thay vì voice ID

Vị trí: `voice/clone.py:161–170`.
DEFAULT_VOICES_BY_LANGUAGE chứa string nhưng matching dùng như list. Tái hiện
Vietnamese profile nhận voice ID `v`. Trả string đầy đủ hoặc dùng structured voice
entries. Đây là lỗi API thư viện; reviewer chưa thấy production caller hiện tại,
nên không tính vào những luồng đang được chứng minh gây lỗi tự động.

## Đối chiếu báo cáo cũ

| Báo cáo 29-10 | Kết quả tại working tree 30-10 |
| --- | --- |
| R01–R08 | Vẫn còn; xem F01, F03, F05–F10. |
| R09–R14 | Vẫn còn; xem F28–F30, F38–F40. |
| R15–R19 | Vẫn còn; xem F13–F17. |
| R20 | Vẫn còn; F52. |
| R21 | Sửa một phần; còn synchronous music analysis tại F57, và capacity cancellation độc lập tại F53. |
| R22–R23 | Vẫn còn; F55–F56. |
| R24 | Đã sửa collision được báo cáo: full-content hash, namespace v3, bỏ cache nếu không đọc được file; regression test đạt. |
| R25 | Đã sửa ở LLMRouter: provider guard chặn live handler trong MOCK; agent tái hiện 0 lần gọi live handler. |
| R26 | Đã sửa: audit malformed ném LLMOutputError, không được đổi thành PASS. |
| R27–R29 | Vẫn còn; xem F41–F43. |
| R30 | Đã sửa việc nhả capacity sớm: hai request timeout chỉ khởi động một extraction bị giữ; slot còn giữ đến khi thread kết thúc. |

## Ưu tiên sửa và mức độ kiểm chứng

Ưu tiên lỗi lộ file/secret và xác thực (F01–F02, F27–F37); sau đó quyền sở hữu
execution và chống đăng trùng (F05–F07, F13–F15, F18, F40). Trước bật tự động
Content OS cần sửa các gate approval, script version và output validation
(F41–F44). Xử lý transaction số dư, ngân sách, admission và trạng thái terminal
trước vận hành nhiều worker. Các deadlock rate limiter/Whisper và regression WAV
header (F49–F51) cũng cần sửa trước chạy pipeline dài; sau đó xử lý resource
lifecycle, timing/media correctness, ranking và UI.

Các cải thiện gần đây về full-content hash, cache RAM có giới hạn, retry có backoff,
MOCK guard và audit fail-closed là tiến bộ có kiểm chứng. Chúng không giải quyết
các state transition/claim/publish race độc lập được liệt kê ở trên.

Đã đối chiếu tài liệu constitution/dependency graph/module map/roadmap, nhưng
không chứng nhận mọi interface frozen và mọi module đã tuân thủ hoàn toàn.
Không chạy ruff/mypy/Black hay chứng nhận coverage/DoD toàn dự án. Không dùng các
sở thích định dạng, globals hoặc thiếu type hint đơn thuần để nâng severity của
các lỗi hành vi. Lượt review chỉ thêm báo cáo; không sửa implementation/test.

## Kiểm chứng và giới hạn

- AST: 247 file Python trong src/scripts/license_server không có lỗi cú pháp.
- `node --check`: 11 file JavaScript đạt; 5 file UI test chạy bằng Node đều đạt.
- Nhóm pytest queue/worker/JobService/database/resource/network/TTS premium:
  47 passed, 1 deselected, 1,78 giây; test async collector được bỏ ở lượt này.
  Có harness chặn kết nối Python ra mạng; provider report ghi 0 live calls.
- Nhóm provider cost/cache bổ sung: 17 passed, 12 deprecation warnings, 1,99 giây.
  Nhóm publishing/automation/autonomous operator/webhook/bot của agent: 58 passed.
  Nhóm media của agent: 48 focused tests passed. Đây là các lượt riêng, có thể
  chồng lặp test, không phải một lần chạy full suite hay tổng số test khác nhau.
- Tái hiện queue và credit tại `/tmp/uvai_review_queue_repros.py`; dữ liệu DB
  phục vụ race dùng thư mục tạm. Các lỗi có bằng chứng tĩnh được ghi rõ riêng.
- Bằng chứng agent: `/tmp/uvai_channel_review_repro.py`,
  `/tmp/uvai_channel_scheduler_repro.py`, `/tmp/uvai_scope_review_repro.py`,
  `/tmp/uvai_web_review_repro_final.py`, `/tmp/uvai_auth_review.py`,
  `/tmp/uvai_frontend_review_repro.py`. Các script dùng sentinel/fake data;
  không publish hay gọi provider thật. Harness cô lập bổ sung cho agent chính:
  `/tmp/uvai_review_offline.py`.
- Module-global asyncio semaphore có thể lỗi khi một integration dùng nhiều
  event loop có contention. Agent tái hiện cơ chế, nhưng caller web và real
  Telegram đã kiểm tra dùng event loop đang chạy; asyncio.run nhánh bot được thấy
  ở MockAdapter. Không tính thành production finding vì chưa có trigger thật.
  Chưa kiểm chứng tải nhiều process; semaphore hiện tại chỉ giới hạn trong process.
- Chưa chạy full suite, build Docker/Windows, tải GPU thật, API mạng xã hội thật,
  hay benchmark throughput/RSS toàn bộ pipeline; không suy ra chúng đã đạt từ
  việc syntax check và test tập trung đều xanh.
- Môi trường Python 3.14.4; CI cấu hình Python 3.11/3.12. Kết quả cục bộ không
  thay thế kiểm tra trên phiên bản production.
